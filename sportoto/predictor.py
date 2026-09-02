"""Üst katman tahmin arayüzü: eğitim, kalibrasyon ve maç tahmini.

Eğitim iki aşamalıdır ve bu ayrım kasıtlıdır:

  1. **Kalibrasyon** — son `blend_fit_window` maç üzerinde yürüyen tahmin
     üretilir (her blok yalnızca kendinden önceki veriyle kestirilmiş
     modellerle) ve blend ağırlıkları bu *sızıntısız* çıktı üzerinde fit edilir.
     Ağırlıkları modellerin eğitildiği veri üzerinde fit etmek, Dixon-Coles'a
     hak etmediği bir ağırlık kazandırırdı.
  2. **Nihai kestirim** — bileşenler tüm veriyle yeniden kestirilir; canlı
     tahminlerde bunlar kullanılır.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Settings
from .models.blend import LogPoolBlend
from .pipeline import (
    COMPONENTS,
    ComponentModels,
    component_probabilities,
    fit_components,
    prepare_frame,
    stack_components,
    walk_forward,
)
from .storage import Database
from .teams import TeamResolver

log = logging.getLogger(__name__)

OUTCOME_LABELS = ("1", "0", "2")


@dataclass
class MatchPrediction:
    home: str
    away: str
    league: str | None
    p_home: float
    p_draw: float
    p_away: float
    #: Bileşen bazında olasılıklar — "neden bu tahmin" açıklaması için.
    components: dict[str, tuple[float, float, float]] = field(default_factory=dict)
    matched_home: str | None = None
    matched_away: str | None = None
    match_confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)

    @property
    def probs(self) -> tuple[float, float, float]:
        return self.p_home, self.p_draw, self.p_away

    @property
    def favourite(self) -> str:
        return OUTCOME_LABELS[int(np.argmax(self.probs))]

    @property
    def entropy(self) -> float:
        """Belirsizlik ölçüsü (0 = kesin, 1 = tamamen belirsiz)."""
        p = np.clip(np.array(self.probs), 1e-12, 1.0)
        return float(-(p * np.log(p)).sum() / np.log(3.0))

    def as_dict(self) -> dict:
        data = asdict(self)
        data["favourite"] = self.favourite
        data["entropy"] = self.entropy
        return data


class Predictor:
    """Veritabanından eğitilir, maç listesi için 1/0/2 olasılığı üretir."""

    def __init__(self, settings: Settings, db: Database | None = None):
        self.settings = settings
        self.db = db or Database(settings.db_path)
        self.frame: pd.DataFrame | None = None
        self.models: ComponentModels | None = None
        self.blend = LogPoolBlend()
        self.resolver: TeamResolver | None = None
        self.calibration: dict = {}

    # -- eğitim -----------------------------------------------------------
    def train(
        self,
        leagues: list[str] | None = None,
        calibrate: bool = True,
        refit_days: int = 21,
        calibration_days: int = 730,
        progress: bool = True,
    ) -> dict:
        """Veriyi hazırlar, blend'i kalibre eder, nihai modelleri kestirir."""
        raw = self.db.load_matches(leagues=leagues, played_only=False)
        if raw.empty:
            raise RuntimeError("Veritabanı boş — önce `ingest` çalıştırın.")

        self.frame = prepare_frame(raw, self.settings)
        played = self.frame[self.frame["y"] >= 0]
        if played.empty:
            raise RuntimeError("Oynanmış maç yok.")

        as_of = played["date"].max() + timedelta(days=1)
        report: dict = {
            "matches": int(len(played)),
            "leagues": sorted(played["league"].dropna().unique().tolist()),
            "first_date": str(played["date"].min().date()),
            "last_date": str(played["date"].max().date()),
        }

        if calibrate:
            start = played["date"].max() - timedelta(days=calibration_days)
            rows = walk_forward(
                played, self.settings, start=start, refit_days=refit_days, progress=progress
            )
            rows = rows[rows["y"] >= 0]
            if len(rows) >= 200:
                self.blend.fit(stack_components(rows), rows["y"].to_numpy())
                report["calibration_matches"] = int(len(rows))
                report["blend"] = self.blend.to_dict()
                log.info("Blend kalibre edildi (%d maç): %s", len(rows), self.blend.describe())
            else:
                log.warning("Kalibrasyon için yeterli maç yok (%d); eşit ağırlık", len(rows))
                self._default_blend()
        else:
            self._default_blend()

        self.models = fit_components(played, self.settings, as_of, leagues=leagues)
        report["dc_leagues"] = self.models.leagues()
        report["as_of"] = str(as_of.date())

        self.resolver = TeamResolver(self.db.known_teams(leagues))
        self.calibration = report
        return report

    def _default_blend(self) -> None:
        """Kalibrasyon yapılamadığında makul sabit ağırlıklar.

        Piyasa en güçlü tek sinyal olduğu için ağırlığın yarısını alır.
        """
        defaults = {"market": 0.50, "dc": 0.30, "elo": 0.15, "form": 0.05}
        self.blend = LogPoolBlend(
            components=list(COMPONENTS),
            weights=defaults,
            temperature=1.0,
            fitted=False,
        )

    # -- tahmin -----------------------------------------------------------
    def predict(self, fixtures: list[dict]) -> list[MatchPrediction]:
        """`fixtures`: {'home','away', opsiyonel 'league','odds_h','odds_d','odds_a'} listesi."""
        if self.models is None or self.frame is None:
            raise RuntimeError("Önce `train()` çağırın.")
        if not fixtures:
            return []

        resolved, warnings_per_row = self._resolve_fixtures(fixtures)
        pending = pd.DataFrame(resolved)
        # Bekleyen maçları geçmişin sonuna ekleyip özellikleri nedensel üret.
        history = self.frame[
            ["league", "season", "date", "home", "away", "fthg", "ftag", "ftr",
             "hst", "ast", "odds_h", "odds_d", "odds_a", "codds_h", "codds_d", "codds_a"]
        ].copy()
        for col in history.columns:
            if col not in pending.columns:
                pending[col] = np.nan
        combined = pd.concat([history, pending[history.columns]], ignore_index=True)
        prepared = prepare_frame(combined, self.settings)
        target = prepared.tail(len(pending)).reset_index(drop=True)

        # Sakatlık/ceza gibi elle girilen düzeltmeleri uygula. Bir kupon tek bir
        # haftanın maçlarından oluştuğu için en erken maç tarihi hepsi için
        # yeterli bir referanstır.
        as_of = str(min(pd.Timestamp(r["date"]) for r in resolved).date())
        adjustments = self.db.active_adjustments(as_of)
        probs = component_probabilities(self.models, target, self.settings, adjustments)
        blended = self.blend.predict(probs)
        floor = self.settings.model.prob_floor
        blended = np.clip(blended, floor, None)
        blended = blended / blended.sum(axis=1, keepdims=True)

        out = []
        for i, row in enumerate(resolved):
            components = {}
            for name in COMPONENTS:
                values = probs.get(name)
                if values is None or np.isnan(values[i]).any():
                    continue
                components[name] = tuple(float(v) for v in values[i])
            out.append(
                MatchPrediction(
                    home=fixtures[i].get("home", row["home"]),
                    away=fixtures[i].get("away", row["away"]),
                    league=row.get("league"),
                    p_home=float(blended[i, 0]),
                    p_draw=float(blended[i, 1]),
                    p_away=float(blended[i, 2]),
                    components=components,
                    matched_home=row["home"],
                    matched_away=row["away"],
                    match_confidence=row.get("_confidence", 1.0),
                    warnings=warnings_per_row[i],
                )
            )
        return out

    def _resolve_fixtures(self, fixtures: list[dict]) -> tuple[list[dict], list[list[str]]]:
        """Kupon adlarını veritabanı takımlarına bağlar, ligi ve tarihi doldurur."""
        if self.resolver is None:
            self.resolver = TeamResolver(self.db.known_teams())
        team_league = self.db.team_leagues()
        assert self.frame is not None
        default_date = self.frame["date"].max() + timedelta(days=1)

        rows, warnings = [], []
        for fixture in fixtures:
            notes: list[str] = []
            home_match = self.resolver.resolve(str(fixture.get("home", "")))
            away_match = self.resolver.resolve(str(fixture.get("away", "")))
            home = home_match.team or str(fixture.get("home", ""))
            away = away_match.team or str(fixture.get("away", ""))

            for label, match in (("Ev sahibi", home_match), ("Deplasman", away_match)):
                if match.team is None:
                    notes.append(f"{label} takım bulunamadı: {match.query!r}")
                elif not match.confident:
                    alts = ", ".join(t for t, _ in match.alternatives[:2])
                    notes.append(
                        f"{label} eşleşmesi belirsiz: {match.query!r} → {match.team!r} "
                        f"(benzerlik {match.score:.2f}; alternatifler: {alts})"
                    )

            league = fixture.get("league") or team_league.get(home) or team_league.get(away)
            date = fixture.get("date")
            rows.append(
                {
                    "league": league,
                    "season": None,
                    "date": pd.Timestamp(date) if date else default_date,
                    "home": home,
                    "away": away,
                    "odds_h": fixture.get("odds_h"),
                    "odds_d": fixture.get("odds_d"),
                    "odds_a": fixture.get("odds_a"),
                    "_confidence": min(home_match.score, away_match.score),
                }
            )
            warnings.append(notes)
        return rows, warnings

    # -- kalıcılık --------------------------------------------------------
    def save_blend(self, path: Path | None = None) -> Path:
        path = Path(path or (self.settings.data_dir / "blend.json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"blend": self.blend.to_dict(), "calibration": self.calibration},
                indent=2, ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def load_blend(self, path: Path | None = None) -> bool:
        path = Path(path or (self.settings.data_dir / "blend.json"))
        if not path.exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8"))
        self.blend = LogPoolBlend.from_dict(data.get("blend", {}))
        self.calibration = data.get("calibration", {})
        return True
