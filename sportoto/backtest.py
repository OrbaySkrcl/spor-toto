"""Yürüyen (walk-forward) backtest ve kupon simülasyonu.

"Model %60 doğru tahmin etti" tek başına anlamsızdır; hedef 15/15. Bu yüzden
iki katman ölçülür:

  1. **Olasılık kalitesi** — log-loss, RPS, Brier ve kalibrasyon. Kupon
     optimizasyonu doğrudan olasılıkların üzerine kurulduğu için, kalibre
     olmayan bir model iyi accuracy'ye rağmen kötü kupon üretir.
  2. **Kupon başarısı** — geçmiş haftalar 15'lik gruplara bölünüp gerçek
     bütçelerle simüle edilir: kaç kez 15/15, 14/15, 13/15 tuttu, TL başına ne
     düştü.

Tüm tahminler `pipeline.walk_forward` üzerinden üretilir; yani bir maçın
tahmininde o maçtan sonraki hiçbir bilgi kullanılmaz.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np
import pandas as pd

from .config import Settings
from .coupon.optimizer import optimize_coupon
from .models.blend import LogPoolBlend
from .pipeline import prepare_frame, stack_components, walk_forward

log = logging.getLogger(__name__)

_EPS = 1e-12


def log_loss(probs: np.ndarray, y: np.ndarray) -> float:
    return float(-np.log(np.clip(probs[np.arange(len(y)), y], _EPS, 1.0)).mean())


def ranked_probability_score(probs: np.ndarray, y: np.ndarray) -> float:
    """RPS — sıralı sonuçlar için doğru metrik.

    1-0-2 sıralı olduğundan (ev / beraberlik / deplasman), "ev galibiyeti
    derken deplasman çıktı" hatası "ev derken beraberlik çıktı"dan daha
    ağır cezalandırılmalıdır. Log-loss bu ayrımı yapmaz, RPS yapar.
    """
    onehot = np.zeros_like(probs)
    onehot[np.arange(len(y)), y] = 1.0
    cum_p = np.cumsum(probs, axis=1)[:, :-1]
    cum_e = np.cumsum(onehot, axis=1)[:, :-1]
    return float(((cum_p - cum_e) ** 2).sum(axis=1).mean() / (probs.shape[1] - 1))


def brier_score(probs: np.ndarray, y: np.ndarray) -> float:
    onehot = np.zeros_like(probs)
    onehot[np.arange(len(y)), y] = 1.0
    return float(((probs - onehot) ** 2).sum(axis=1).mean())


def accuracy(probs: np.ndarray, y: np.ndarray) -> float:
    return float((probs.argmax(axis=1) == y).mean())


def calibration_table(probs: np.ndarray, y: np.ndarray, bins: int = 10) -> pd.DataFrame:
    """Tahmin edilen olasılık kovalarında gerçekleşme oranı."""
    flat_p = probs.ravel()
    onehot = np.zeros_like(probs)
    onehot[np.arange(len(y)), y] = 1.0
    flat_y = onehot.ravel()
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(flat_p, edges) - 1, 0, bins - 1)
    rows = []
    for b in range(bins):
        mask = idx == b
        if not mask.any():
            continue
        rows.append(
            {
                "kova": f"{edges[b]:.1f}-{edges[b+1]:.1f}",
                "n": int(mask.sum()),
                "tahmin": float(flat_p[mask].mean()),
                "gerçek": float(flat_y[mask].mean()),
                "fark": float(flat_p[mask].mean() - flat_y[mask].mean()),
            }
        )
    return pd.DataFrame(rows)


def metrics(probs: np.ndarray, y: np.ndarray) -> dict:
    return {
        "n": int(len(y)),
        "accuracy": accuracy(probs, y),
        "log_loss": log_loss(probs, y),
        "rps": ranked_probability_score(probs, y),
        "brier": brier_score(probs, y),
    }


@dataclass
class CouponSimulation:
    """Belirli bir bütçe için geçmiş kupon simülasyonunun sonucu."""

    budget_columns: int
    coupons: int
    column_price: float
    hits: dict[int, int] = field(default_factory=dict)      # doğru sayısı -> kaç kupon
    expected_hits: dict[int, float] = field(default_factory=dict)
    total_cost: float = 0.0

    def rate(self, k: int) -> float:
        return self.hits.get(k, 0) / self.coupons if self.coupons else 0.0

    def at_least(self, k: int) -> int:
        return sum(v for h, v in self.hits.items() if h >= k)


@dataclass
class BacktestResult:
    rows: pd.DataFrame
    overall: dict
    per_component: dict
    per_season: pd.DataFrame
    calibration: pd.DataFrame
    blend: LogPoolBlend
    simulations: list[CouponSimulation] = field(default_factory=list)

    def report(self) -> str:
        lines = ["=" * 74, "BACKTEST SONUÇLARI".center(74), "=" * 74]
        o = self.overall
        lines.append(
            f"\nDeğerlendirilen maç: {o['n']:,}".replace(",", ".")
            + f"   ({self.rows['date'].min().date()} → {self.rows['date'].max().date()})"
        )
        lines.append(f"Blend {self.blend.describe()}\n")
        lines.append(f"{'Model':<12}{'n':>7}{'İsabet':>9}{'LogLoss':>10}{'RPS':>9}{'Brier':>9}")
        lines.append("-" * 74)
        for name, m in self.per_component.items():
            lines.append(
                f"{name:<12}{m['n']:>7,}{m['accuracy']:>9.3f}{m['log_loss']:>10.4f}"
                f"{m['rps']:>9.4f}{m['brier']:>9.4f}".replace(",", ".")
            )
        lines.append("-" * 74)
        lines.append(
            f"{'BLEND':<12}{o['n']:>7,}{o['accuracy']:>9.3f}{o['log_loss']:>10.4f}"
            f"{o['rps']:>9.4f}{o['brier']:>9.4f}".replace(",", ".")
        )
        if self.simulations:
            lines.append("\n" + "KUPON SİMÜLASYONU (15 maçlık gruplar)".center(74))
            lines.append("-" * 74)
            lines.append(
                f"{'Kolon':>8}{'Maliyet/kupon':>15}{'Kupon':>8}"
                f"{'15/15':>9}{'≥14':>8}{'≥13':>8}{'≥12':>8}{'Bek.15/15':>12}"
            )
            for sim in self.simulations:
                cost = sim.budget_columns * sim.column_price
                lines.append(
                    f"{sim.budget_columns:>8,}{cost:>14,.0f}₺{sim.coupons:>8}"
                    f"{sim.at_least(15):>9}{sim.at_least(14):>8}{sim.at_least(13):>8}"
                    f"{sim.at_least(12):>8}{sim.expected_hits.get(15, 0.0)*sim.coupons:>12.3f}".replace(",", ".")
                )
        return "\n".join(lines)


def run_backtest(
    settings: Settings,
    db=None,
    leagues: list[str] | None = None,
    start: str | None = None,
    end: str | None = None,
    refit_days: int = 21,
    calibration_days: int = 730,
    budgets: list[int] | None = None,
    progress: bool = True,
) -> BacktestResult:
    """Sızıntısız yürüyen backtest çalıştırır.

    Blend ağırlıkları **değerlendirme penceresinden önceki** dönemde kalibre
    edilir; böylece raporlanan skorlar gerçekten örnek dışıdır.
    """
    from .storage import Database

    db = db or Database(settings.db_path)
    raw = db.load_matches(leagues=leagues, played_only=True)
    if raw.empty:
        raise RuntimeError("Veritabanı boş — önce `ingest` çalıştırın.")

    frame = prepare_frame(raw, settings)
    frame = frame[frame["y"] >= 0]

    last = frame["date"].max()
    eval_start = pd.Timestamp(start) if start else last - timedelta(days=730)
    eval_end = pd.Timestamp(end) if end else last
    calib_start = eval_start - timedelta(days=calibration_days)

    # 1) Kalibrasyon penceresi: blend ağırlıklarını burada öğren.
    log.info("Kalibrasyon penceresi: %s → %s", calib_start.date(), eval_start.date())
    calib_rows = walk_forward(
        frame, settings, start=calib_start, end=eval_start - timedelta(days=1),
        refit_days=refit_days, progress=progress,
    )
    blend = LogPoolBlend()
    if len(calib_rows) >= 200:
        blend.fit(stack_components(calib_rows), calib_rows["y"].to_numpy())
    else:
        blend = LogPoolBlend(
            components=["dc", "elo", "market", "form"],
            weights={"market": 0.50, "dc": 0.30, "elo": 0.15, "form": 0.05},
        )
        log.warning("Kalibrasyon verisi yetersiz; sabit ağırlık kullanıldı")

    # 2) Değerlendirme penceresi: dokunulmamış veri.
    log.info("Değerlendirme penceresi: %s → %s", eval_start.date(), eval_end.date())
    rows = walk_forward(
        frame, settings, start=eval_start, end=eval_end,
        refit_days=refit_days, progress=progress,
    )
    if rows.empty:
        raise RuntimeError(
            f"Değerlendirme penceresinde ({eval_start.date()} → {eval_end.date()}) maç yok. "
            f"Veritabanı {frame['date'].min().date()} ile {frame['date'].max().date()} "
            "arasını kapsıyor; --start / --end değerlerini bu aralığa alın."
        )
    rows = rows[rows["y"] >= 0].reset_index(drop=True)

    stacks = stack_components(rows)
    blended = blend.predict(stacks)
    floor = settings.model.prob_floor
    blended = np.clip(blended, floor, None)
    blended = blended / blended.sum(axis=1, keepdims=True)
    rows["p_home"], rows["p_draw"], rows["p_away"] = blended.T

    y = rows["y"].to_numpy()
    per_component = {}
    for name, values in stacks.items():
        mask = ~np.isnan(values).any(axis=1)
        if mask.sum() >= 50:
            per_component[name] = metrics(values[mask], y[mask])

    per_season = _per_period(rows, blended, y)
    result = BacktestResult(
        rows=rows,
        overall=metrics(blended, y),
        per_component=per_component,
        per_season=per_season,
        calibration=calibration_table(blended, y),
        blend=blend,
        simulations=simulate_coupons(
            rows, budgets or [1, 24, 96, 576, 3072], settings.coupon.column_price
        ),
    )
    return result


def _per_period(rows: pd.DataFrame, probs: np.ndarray, y: np.ndarray) -> pd.DataFrame:
    period = rows["date"].dt.to_period("Y").astype(str)
    out = []
    for label in sorted(period.unique()):
        mask = (period == label).to_numpy()
        if mask.sum() < 30:
            continue
        m = metrics(probs[mask], y[mask])
        m["dönem"] = label
        out.append(m)
    return pd.DataFrame(out)


def simulate_coupons(
    rows: pd.DataFrame, budgets: list[int], column_price: float, coupon_size: int = 15
) -> list[CouponSimulation]:
    """Geçmiş maçları 15'lik kuponlara bölüp her bütçeyi simüle eder.

    Gruplama tarihe göre yapılır; gerçek Spor Toto listesi de bir haftanın
    maçlarından oluştuğu için bu yapı gerçeğe yakındır.
    """
    ordered = rows.sort_values("date").reset_index(drop=True)
    n_coupons = len(ordered) // coupon_size
    if n_coupons == 0:
        return []

    sims = []
    for budget in budgets:
        sim = CouponSimulation(budget_columns=budget, coupons=n_coupons, column_price=column_price)
        hits: dict[int, int] = {}
        expected: dict[int, float] = {}
        for c in range(n_coupons):
            chunk = ordered.iloc[c * coupon_size : (c + 1) * coupon_size]
            probs = list(zip(chunk["p_home"], chunk["p_draw"], chunk["p_away"]))
            plan = optimize_coupon(probs, max_columns=budget, column_price=column_price)
            truth = chunk["y"].to_numpy()
            correct = sum(
                1
                for i, selection in enumerate(plan.selections)
                if ("1", "0", "2")[truth[i]] in selection.picks
            )
            hits[correct] = hits.get(correct, 0) + 1
            for k, p in plan.distribution.items():
                expected[k] = expected.get(k, 0.0) + p / n_coupons
            sim.total_cost += plan.cost
        sim.hits = hits
        sim.expected_hits = expected
        sims.append(sim)
    return sims
