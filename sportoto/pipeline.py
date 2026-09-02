"""Özellik hazırlama ve **sızıntısız** yürüyen (walk-forward) tahmin üretimi.

Tasarım ilkesi: bir maçın tahmininde kullanılan her şey o maçtan *önce*
bilinebilir olmalı.

  * Elo ve form özellikleri zaten nedensel üretiliyor (tek geçiş, yalnızca
    geçmiş maçlarla) — bu yüzden tüm tarih için bir kez hesaplanabilirler.
  * Model *parametreleri* (Dixon-Coles, sıralı Elo, form regresyonu) ise
    tarihe bağlı; bunlar `refit_days` aralıklarla, yalnızca o ana kadarki
    veriyle yeniden kestirilir.

Aynı fonksiyon hem backtest'i hem de blend ağırlıklarının kalibrasyonunu
besler; böylece "değerlendirmede kullanılan boru hattı" ile "üretimde
kullanılan boru hattı" birebir aynıdır.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
import pandas as pd

from .config import Settings
from .features.elo import EloConfig, build_elo_history
from .features.form import build_form_features
from .models.dixon_coles import DixonColes, DixonColesFit
from .models.elo_model import EloOrdinal
from .models.form_model import FormModel, design_matrix
from .models.market import market_frame

log = logging.getLogger(__name__)

#: Olasılık sütun sırası her yerde (1, 0, 2) = (ev, beraberlik, deplasman).
OUTCOME_ORDER = ("H", "D", "A")
_OUTCOME_INDEX = {"H": 0, "D": 1, "A": 2}
COMPONENTS = ("dc", "elo", "market", "form")

#: Dixon-Coles'un kestirimde kullanacağı geriye bakış penceresi (gün).
DC_LOOKBACK_DAYS = 4 * 365


def outcome_index(series) -> np.ndarray:
    """'H'/'D'/'A' -> 0/1/2. Bilinmeyen -> -1."""
    return np.asarray([_OUTCOME_INDEX.get(str(v), -1) for v in series], dtype=int)


def prepare_frame(df: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Ham maç tablosuna Elo, form ve piyasa olasılığı sütunlarını ekler."""
    if df.empty:
        return df
    elo_config = EloConfig(
        k=settings.model.elo_k,
        home_advantage=settings.model.elo_home_advantage,
        start=settings.model.elo_start,
        season_regression=settings.model.elo_season_regression,
    )
    frame, _ = build_elo_history(df, elo_config)
    frame = build_form_features(frame)
    frame = market_frame(frame, settings.model.margin_method)
    frame["y"] = outcome_index(frame["ftr"])
    return frame


@dataclass
class ComponentModels:
    """Belirli bir ana kadarki veriyle kestirilmiş bileşen modelleri."""

    as_of: pd.Timestamp
    dc: dict[str, DixonColesFit] = field(default_factory=dict)
    elo: EloOrdinal = field(default_factory=EloOrdinal)
    form: FormModel = field(default_factory=FormModel)
    dc_engine: DixonColes | None = None

    def leagues(self) -> list[str]:
        return sorted(self.dc)


def fit_components(
    history: pd.DataFrame,
    settings: Settings,
    as_of: pd.Timestamp,
    leagues: list[str] | None = None,
) -> ComponentModels:
    """`history` (yalnızca `as_of`'tan önceki maçlar) üzerinde bileşenleri kestirir."""
    model_config = settings.model
    engine = DixonColes(xi=model_config.dc_xi, max_goals=model_config.dc_max_goals)
    models = ComponentModels(as_of=as_of, dc_engine=engine)

    played = history.dropna(subset=["fthg", "ftag"])
    if played.empty:
        return models

    # --- Dixon-Coles: lig başına ---
    cutoff = as_of - timedelta(days=DC_LOOKBACK_DAYS)
    targets = leagues or sorted(played["league"].dropna().unique())
    for code in targets:
        subset = played[(played["league"] == code) & (played["date"] >= cutoff)]
        if len(subset) < model_config.dc_min_matches:
            continue
        try:
            models.dc[code] = engine.fit(subset, as_of=as_of, league=code)
        except Exception as exc:
            log.warning("Dixon-Coles kestirimi başarısız (%s): %s", code, exc)

    # --- Sıralı Elo ve form modeli: tüm ligler birlikte ---
    labelled = played[played["y"] >= 0]
    if len(labelled) >= 200:
        age = (as_of - labelled["date"]).dt.days.to_numpy().astype(float)
        weights = np.exp(-model_config.dc_xi * np.clip(age, 0, None))
        models.elo = EloOrdinal().fit(
            labelled["elo_diff"].to_numpy(), labelled["y"].to_numpy(), weights
        )
        models.form = FormModel().fit(
            design_matrix(labelled), labelled["y"].to_numpy(), weights
        )
    return models


def component_probabilities(
    models: ComponentModels,
    frame: pd.DataFrame,
    settings: Settings,
    adjustments: dict[str, tuple[float, float]] | None = None,
) -> dict[str, np.ndarray]:
    """Her bileşen için (N, 3) olasılık dizisi. Uygulanamayan satırlar NaN.

    `adjustments`: takım -> (atak, defans) logaritmik düzeltme. Ücretsiz
    kaynakta bulunmayan sakatlık/ceza bilgisini elle beslemek için; yalnızca
    Dixon-Coles gol beklentilerine uygulanır (bkz. `storage.adjustments`).
    """
    n = len(frame)
    out: dict[str, np.ndarray] = {}

    # --- Dixon-Coles ---
    dc_probs = np.full((n, 3), np.nan)
    engine = models.dc_engine or DixonColes(
        xi=settings.model.dc_xi, max_goals=settings.model.dc_max_goals
    )
    leagues = frame["league"].to_numpy()
    homes = frame["home"].to_numpy()
    aways = frame["away"].to_numpy()
    min_team = settings.model.dc_min_team_matches
    for i in range(n):
        fit = models.dc.get(leagues[i])
        if fit is None or not (fit.knows(homes[i]) and fit.knows(aways[i])):
            continue
        # Modele az maçla girmiş takımlar için kestirim güvenilmez.
        if min(fit.team_matches.get(homes[i], 0.0),
               fit.team_matches.get(aways[i], 0.0)) < min_team:
            continue
        dc_probs[i] = engine.outcome_probabilities(fit, homes[i], aways[i], adjustments)
    out["dc"] = dc_probs

    # --- Sıralı Elo ---
    out["elo"] = models.elo.predict(frame["elo_diff"].to_numpy())

    # --- Piyasa ---
    market = np.full((n, 3), np.nan)
    if {"mkt_h", "mkt_d", "mkt_a"}.issubset(frame.columns):
        market = frame[["mkt_h", "mkt_d", "mkt_a"]].to_numpy(dtype=float)
    out["market"] = market

    # --- Form ---
    out["form"] = (
        models.form.predict(design_matrix(frame))
        if models.form.fitted
        else np.full((n, 3), np.nan)
    )
    return out


def walk_forward(
    frame: pd.DataFrame,
    settings: Settings,
    start: pd.Timestamp,
    end: pd.Timestamp | None = None,
    refit_days: int = 14,
    progress: bool = False,
) -> pd.DataFrame:
    """`start`–`end` arasındaki her maç için sızıntısız bileşen olasılıkları üretir.

    Modeller `refit_days` günde bir, yalnızca o ana kadarki veriyle yeniden
    kestirilir. Bir tahmin bloğunda kullanılan modeller, o bloğun ilk maçından
    kesinlikle önce kestirilmiştir.
    """
    frame = frame.sort_values("date").reset_index(drop=True)
    end = pd.Timestamp(end) if end is not None else frame["date"].max()
    window = frame[(frame["date"] >= start) & (frame["date"] <= end)]
    if window.empty:
        return pd.DataFrame()

    chunks = []
    cursor = pd.Timestamp(start)
    total_blocks = max(1, int((pd.Timestamp(end) - cursor).days / refit_days) + 1)
    block = 0

    while cursor <= end:
        stop = cursor + timedelta(days=refit_days)
        history = frame[frame["date"] < cursor]
        # Son blok `stop` sınırıyla `end`'i aşabilir; `end` üst sınırı burada
        # da uygulanmalı. Aksi hâlde backtest'in kalibrasyon penceresi
        # değerlendirme penceresine taşar ve skorlar örnek dışı olmaktan çıkar.
        batch = frame[
            (frame["date"] >= cursor) & (frame["date"] < stop) & (frame["date"] <= end)
        ]
        block += 1
        if batch.empty:
            cursor = stop
            continue
        if history.empty:
            cursor = stop
            continue

        models = fit_components(history, settings, cursor)
        probs = component_probabilities(models, batch, settings)

        piece = batch[
            [c for c in ("date", "league", "home", "away", "ftr", "y", "elo_diff") if c in batch]
        ].copy()
        for name, values in probs.items():
            piece[f"{name}_h"] = values[:, 0]
            piece[f"{name}_d"] = values[:, 1]
            piece[f"{name}_a"] = values[:, 2]
        chunks.append(piece)

        if progress and block % 10 == 0:
            log.info("walk-forward %d/%d blok (%s)", block, total_blocks, cursor.date())
        cursor = stop

    if not chunks:
        return pd.DataFrame()
    return pd.concat(chunks, ignore_index=True)


def stack_components(rows: pd.DataFrame) -> dict[str, np.ndarray]:
    """`walk_forward` çıktısını blend'in beklediği sözlüğe çevirir."""
    out = {}
    for name in COMPONENTS:
        cols = [f"{name}_h", f"{name}_d", f"{name}_a"]
        if all(c in rows.columns for c in cols):
            out[name] = rows[cols].to_numpy(dtype=float)
    return out
