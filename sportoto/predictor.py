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
from .models.calibration import VectorScaling
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


def _clean(value):
    """NaN/None değerleri None'a indirger (pandas satırlarından okurken gerekir)."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)


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
    date: str | None = None
    #: Veritabanındaki fikstürle eşleştiyse o maçın kimliği. Tahmin geçmişini
    #: gerçek sonuçlarla karşılaştırabilmek için gerekir.
    match_id: str | None = None
    #: Hiçbir model bileşeni bu maça uygulanamadıysa True. Olasılıklar o zaman
    #: %33/%33/%33'tür ve bu bir tahmin değil, "bilmiyorum" demektir — çıktıda
    #: gerçek bir tahminmiş gibi gösterilmemelidir.
    no_data: bool = False
    #: Tahmin üretildi ama dayanağı zayıf: takımlardan biri tanınmadı ya da
    #: gol modeli uygulanamadı. Kullanıcı bu satıra daha az güvenmeli.
    low_data: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def probs(self) -> tuple[float, float, float]:
        return self.p_home, self.p_draw, self.p_away

    @property
    def favourite(self) -> str:
        return OUTCOME_LABELS[int(np.argmax(self.probs))]

    @property
    def confidence(self) -> float:
        """En olası sonucun olasılığı — "bu tahmine ne kadar güveniyoruz"."""
        return float(max(self.probs))

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
        self.calibrator = VectorScaling()
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
        # Kalibrasyon atlanırsa (kayıtlı blend yeniden kullanılıyorsa) daha önce
        # ölçülmüş başarı rakamını koru — yoksa `basari` komutu, aslında ölçüm
        # yapılmış olmasına rağmen "ölçülmedi" der.
        previous_quality = self.calibration.get("quality") if self.calibration else None
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
            rows = rows[rows["y"] >= 0].sort_values("date").reset_index(drop=True)
            if len(rows) >= 200:
                # Önce zamana göre 70/30 böl: ilk parçada ağırlıkları öğren,
                # son parçada ölç. Böylece raporlanan başarı yüzdesi gerçekten
                # örnek dışıdır — kullanıcıya söylediğimiz rakam dürüst olur.
                report["quality"] = self._measure_quality(rows)
                # Ölçüm bittikten sonra ağırlıkları tüm pencereyle yeniden fit et.
                self.blend = LogPoolBlend().fit(
                    stack_components(rows), rows["y"].to_numpy()
                )
                # Sınıf bazlı kayma düzeltmesi blend'in üstüne oturur; en çok
                # beraberlikte fayda sağlar ve kupon dağılımını doğrudan etkiler.
                self.calibrator = VectorScaling().fit(
                    self.blend.predict(stack_components(rows)), rows["y"].to_numpy()
                )
                report["calibration_matches"] = int(len(rows))
                report["blend"] = self.blend.to_dict()
                report["class_calibration"] = self.calibrator.to_dict()
                log.info(self.calibrator.describe())
                log.info("Blend kalibre edildi (%d maç): %s", len(rows), self.blend.describe())
            else:
                log.warning("Kalibrasyon için yeterli maç yok (%d); eşit ağırlık", len(rows))
                self._default_blend()
        else:
            self._default_blend()

        if not report.get("quality") and previous_quality:
            report["quality"] = previous_quality

        self.models = fit_components(played, self.settings, as_of, leagues=leagues)
        report["dc_leagues"] = self.models.leagues()
        report["drift"] = self.models.drift.to_dict()
        if self.models.drift.fitted:
            log.info(self.models.drift.describe())
        report["as_of"] = str(as_of.date())

        self.resolver = TeamResolver(self.db.known_teams(leagues))
        self.calibration = report
        return report

    def _measure_quality(self, rows: pd.DataFrame) -> dict:
        """Örnek dışı başarı ölçer: zamana göre 70/30 böl, sonda değerlendir.

        Kullanıcıya "model ne kadar isabetli" diye söylenen rakamın, ağırlıkların
        öğrenildiği veriden gelmemesi gerekir. Bu yüzden ölçüm ayrı bir dilimde
        yapılır ve üretim ağırlıkları sonradan tüm pencereyle yeniden fit edilir.
        """
        from .backtest import metrics

        split = int(len(rows) * 0.7)
        train_rows, test_rows = rows.iloc[:split], rows.iloc[split:]
        if len(test_rows) < 100:
            return {}
        try:
            probe = LogPoolBlend().fit(
                stack_components(train_rows), train_rows["y"].to_numpy(),
                fit_profiles=False,
            )
            probe_cal = VectorScaling().fit(
                probe.predict(stack_components(train_rows)), train_rows["y"].to_numpy()
            )
            probs = probe_cal.apply(probe.predict(stack_components(test_rows)))
        except Exception as exc:
            log.warning("Başarı ölçümü yapılamadı: %s", exc)
            return {}

        result = metrics(probs, test_rows["y"].to_numpy())
        result["favourite_hit_rate"] = result.pop("accuracy")
        result["first_date"] = str(test_rows["date"].min().date())
        result["last_date"] = str(test_rows["date"].max().date())
        # Referans: her maçta lig taban oranını oynamak.
        base = np.bincount(train_rows["y"].to_numpy(), minlength=3) / len(train_rows)
        result["baseline_hit_rate"] = float(base.max())
        return result

    @property
    def quality(self) -> dict:
        """En son ölçülen örnek dışı başarı (yoksa boş)."""
        return self.calibration.get("quality", {})

    # -- haftalık fikstür -------------------------------------------------
    def upcoming(self, days: int = 8, leagues: list[str] | None = None) -> list[MatchPrediction]:
        """Veritabanındaki oynanmamış maçlar için tahmin üretir.

        Fikstürler `ingest` sırasında kaynaktan (güncel oranlarıyla birlikte)
        indirilir; burada yalnızca okunup tahmine verilirler.
        """
        fixtures = self.db.load_fixtures(days=days, leagues=leagues)
        if fixtures.empty:
            return []
        payload = [
            {
                "home": row["home"],
                "away": row["away"],
                "league": row["league"],
                "date": row["date"],
                "match_id": row["match_id"],
                "odds_h": _clean(row.get("odds_h")),
                "odds_d": _clean(row.get("odds_d")),
                "odds_a": _clean(row.get("odds_a")),
            }
            for _, row in fixtures.iterrows()
        ]
        predictions = self.predict(payload)
        for prediction, (_, row) in zip(predictions, fixtures.iterrows()):
            prediction.date = row["date"].date().isoformat()
            prediction.match_id = row["match_id"]
        return predictions

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
        # Geçmiş yalnızca **oynanmış** maçlardan oluşmalı: `self.frame` fikstürleri
        # de içerir ve onlar `pending` olarak zaten eklenecektir; süzmezsek aynı
        # maç iki kez görünür.
        history = self.frame[self.frame["y"] >= 0][
            ["league", "season", "date", "home", "away", "fthg", "ftag", "ftr",
             "hst", "ast", "odds_h", "odds_d", "odds_a", "codds_h", "codds_d", "codds_a"]
        ].copy()
        for col in history.columns:
            if col not in pending.columns:
                pending[col] = np.nan
        # Satırları konumla değil, açık bir kimlikle geri eşliyoruz.
        # `prepare_frame` tarihe göre sıralama yapar; bekleyen maçların hepsi
        # aynı tarihi paylaştığı için konum tabanlı bir seçim (ör. `.tail(n)`)
        # onları birbirine karıştırır ve tahminler yanlış maça bağlanır.
        history = history.copy()
        history["_row_id"] = -1
        pending = pending.copy()
        pending["_row_id"] = np.arange(len(pending))
        columns = list(history.columns)
        combined = pd.concat([history, pending[columns]], ignore_index=True)
        prepared = prepare_frame(combined, self.settings)
        target = (
            prepared[prepared["_row_id"] >= 0]
            .sort_values("_row_id", kind="mergesort")
            .reset_index(drop=True)
        )
        if len(target) != len(pending):
            raise RuntimeError(
                f"Tahmin satırları eşleşmedi: {len(target)} ≠ {len(pending)}"
            )

        # Sakatlık/ceza gibi elle girilen düzeltmeleri uygula. Bir kupon tek bir
        # haftanın maçlarından oluştuğu için en erken maç tarihi hepsi için
        # yeterli bir referanstır.
        as_of = str(min(pd.Timestamp(r["date"]) for r in resolved).date())
        adjustments = self.db.active_adjustments(as_of)
        probs = component_probabilities(self.models, target, self.settings, adjustments)
        blended = self.calibrator.apply(self.blend.predict(probs))
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
            notes = list(warnings_per_row[i])
            if not components:
                notes.append(
                    f"{row['home']} - {row['away']}: model bu maç için veri bulamadı "
                    "(takımlar veritabanında yok veya çok az maç var)"
                )
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
                    match_id=row.get("_match_id"),
                    date=(
                        pd.Timestamp(row["date"]).date().isoformat()
                        if row.get("date") is not None
                        else None
                    ),
                    match_confidence=row.get("_confidence", 1.0),
                    no_data=not components,
                    low_data=bool(components)
                    and (row.get("_unresolved", False) or "dc" not in components),
                    warnings=notes,
                )
            )
        return out

    def _resolve_fixtures(self, fixtures: list[dict]) -> tuple[list[dict], list[list[str]]]:
        """Kupon adlarını veritabanı takımlarına bağlar, ligi ve tarihi doldurur."""
        if self.resolver is None:
            self.resolver = TeamResolver(self.db.known_teams())
        team_league = self.db.team_leagues()
        # Yapıştırılan listede oran yoktur; aynı maçın güncel oranları
        # veritabanında duruyorsa oradan alınır.
        upcoming = self.db.upcoming_index()
        assert self.frame is not None
        default_date = self.frame["date"].max() + timedelta(days=1)

        rows, warnings = [], []
        for fixture in fixtures:
            notes: list[str] = []
            home_match = self.resolver.resolve(str(fixture.get("home", "")))
            away_match = self.resolver.resolve(str(fixture.get("away", "")))
            # Kabul eşiğinin altındaki eşleşmeyi kullanmak, alakasız bir takımın
            # gücüyle tahmin üretmek demektir. O takımı bilinmeyen bırakırız:
            # model onun için susar ve kullanıcı durumu görür.
            home = home_match.team if home_match.usable else str(fixture.get("home", ""))
            away = away_match.team if away_match.usable else str(fixture.get("away", ""))
            unresolved = not (home_match.usable and away_match.usable)

            for label, match in (("Ev sahibi", home_match), ("Deplasman", away_match)):
                if not match.usable:
                    notes.append(
                        f"{label} takım tanınmadı: {match.query!r} "
                        "— bu maç için veritabanında karşılık yok"
                    )
                elif not match.confident:
                    alts = ", ".join(t for t, _ in match.alternatives[:2])
                    notes.append(
                        f"{label} eşleşmesi belirsiz: {match.query!r} → {match.team!r} "
                        f"(benzerlik {match.score:.2f}; alternatifler: {alts})"
                    )

            league = fixture.get("league") or team_league.get(home) or team_league.get(away)
            date = fixture.get("date")
            odds = (fixture.get("odds_h"), fixture.get("odds_d"), fixture.get("odds_a"))
            match_id = fixture.get("match_id")

            stored = upcoming.get((home, away))
            if stored is not None:
                match_id = match_id or stored["match_id"]
                league = league or stored["league"]
                if date is None:
                    date = stored["date"]
                if all(v is None for v in odds):
                    odds = (stored["odds_h"], stored["odds_d"], stored["odds_a"])
                    if odds[0] is not None:
                        notes.append(
                            f"{home} - {away}: güncel bahis oranları kullanıldı"
                        )

            rows.append(
                {
                    "league": league,
                    "season": None,
                    "date": pd.Timestamp(date) if date is not None else default_date,
                    "home": home,
                    "away": away,
                    "odds_h": _clean(odds[0]),
                    "odds_d": _clean(odds[1]),
                    "odds_a": _clean(odds[2]),
                    "_confidence": min(home_match.score, away_match.score),
                    "_unresolved": unresolved,
                    "_match_id": match_id,
                }
            )
            warnings.append(notes)
        return rows, warnings

    def record(self, predictions: list[MatchPrediction]) -> int:
        """Tahminleri geçmiş kaydına yazar (sonradan gerçek sonuçla ölçmek için)."""
        return self.db.save_predictions(
            [
                {
                    "match_id": p.match_id,
                    "p_home": p.p_home,
                    "p_draw": p.p_draw,
                    "p_away": p.p_away,
                }
                for p in predictions
            ]
        )

    def track_record(self, limit: int = 500) -> dict:
        """Kaydedilmiş tahminlerin gerçekleşen sonuçlara karşı performansı."""
        from .backtest import metrics
        from .pipeline import outcome_index

        history = self.db.prediction_history(limit=limit)
        if history.empty:
            return {"n": 0, "pending": self.db.pending_prediction_count()}

        y = outcome_index(history["ftr"])
        keep = y >= 0
        history, y = history[keep].reset_index(drop=True), y[keep]
        if not len(y):
            return {"n": 0, "pending": self.db.pending_prediction_count()}

        probs = history[["p_home", "p_draw", "p_away"]].to_numpy(dtype=float)
        result = metrics(probs, y)
        result["favourite_hit_rate"] = result.pop("accuracy")
        result["first_date"] = str(history["date"].min().date())
        result["last_date"] = str(history["date"].max().date())
        result["pending"] = self.db.pending_prediction_count()

        # Güven bandına göre kırılım: model "güçlü" derken gerçekten haklı mı?
        confidence = probs.max(axis=1)
        hit = probs.argmax(axis=1) == y
        bands = []
        for low, high, label in ((0.0, 0.45, "belirsiz/zayıf"), (0.45, 0.60, "orta"),
                                 (0.60, 1.01, "güçlü")):
            mask = (confidence >= low) & (confidence < high)
            if mask.sum() >= 5:
                bands.append({
                    "label": label,
                    "n": int(mask.sum()),
                    "hit_rate": float(hit[mask].mean()),
                    "claimed": float(confidence[mask].mean()),
                })
        result["bands"] = bands
        return result

    # -- kalıcılık --------------------------------------------------------
    def save_blend(self, path: Path | None = None) -> Path:
        path = Path(path or (self.settings.data_dir / "blend.json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "blend": self.blend.to_dict(),
                    "class_calibration": self.calibrator.to_dict(),
                    "calibration": self.calibration,
                },
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
        self.calibrator = VectorScaling.from_dict(data.get("class_calibration", {}))
        self.calibration = data.get("calibration", {})
        return True
