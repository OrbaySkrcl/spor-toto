"""Bahis oranlarını olasılığa çevirme (marj / overround temizleme).

Bir kitapçının oranlarının tersleri 1'den fazla toplar; fark kitapçı marjıdır
(overround). Marjı doğru dağıtmak önemlidir çünkü **eşit dağıtmak favori
lehine sistematik hata yapar** — uzun kuyruk (sürpriz sonuç) oranları
orantısız yüklüdür (favourite–longshot bias).

Üç yöntem:
  * ``basic``  — tersleri normalize eder. Hızlı ama yanlı.
  * ``power``  — p_i ∝ (1/o_i)^k, k'yı Σp=1 olacak şekilde çözer. Kuyruğu düzeltir.
  * ``shin``   — bilgili bahisçi oranını (z) kestirir; teorik olarak en sağlam.

Varsayılan ``power``: Shin kadar iyi çalışır, sayısal olarak daha kararlıdır.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

_EPS = 1e-12


def remove_margin(odds: list[float], method: str = "power") -> list[float]:
    """Ondalık oranları toplamı 1 olan olasılıklara çevirir."""
    valid = [o for o in odds if o and o > 1.0]
    if len(valid) != len(odds) or not odds:
        raise ValueError(f"Geçersiz oran seti: {odds}")

    raw = [1.0 / o for o in odds]
    total = sum(raw)
    if total <= 1.0 + 1e-9:
        # Marj yok (veya negatif): doğrudan normalize et.
        return [r / total for r in raw]

    method = (method or "power").lower()
    if method == "basic":
        return [r / total for r in raw]
    if method == "shin":
        return _shin(raw)
    if method == "power":
        return _power(raw)
    raise ValueError(f"Bilinmeyen marj yöntemi: {method}")


def _power(raw: list[float], tol: float = 1e-10, max_iter: int = 200) -> list[float]:
    """p_i = raw_i^k, Σp_i = 1 olacak k'yı ikili aramayla bulur.

    Σ raw_i^k, k'ya göre azalandır (raw_i < 1 olduğundan), dolayısıyla ikili
    arama garantili yakınsar.
    """
    lo, hi = 1.0, 1.0
    # Üst sınırı, toplam 1'in altına düşene kadar büyüt.
    for _ in range(60):
        if sum(r**hi for r in raw) <= 1.0:
            break
        hi *= 1.5
    else:
        return [r / sum(raw) for r in raw]

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        s = sum(r**mid for r in raw)
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = mid
        else:
            hi = mid
    k = 0.5 * (lo + hi)
    probs = [r**k for r in raw]
    total = sum(probs)
    return [p / total for p in probs]


def _shin(raw: list[float], tol: float = 1e-10, max_iter: int = 200) -> list[float]:
    """Shin (1993) yöntemi: bilgili bahisçi payı z'yi çözer."""
    total = sum(raw)
    lo, hi = 0.0, 0.99

    def probs_for(z: float) -> list[float]:
        out = []
        for r in raw:
            inner = z * z + 4.0 * (1.0 - z) * (r * r) / total
            out.append((math.sqrt(max(inner, 0.0)) - z) / (2.0 * (1.0 - z) + _EPS))
        return out

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        s = sum(probs_for(mid))
        if abs(s - 1.0) < tol:
            break
        if s > 1.0:
            lo = mid
        else:
            hi = mid
    result = probs_for(0.5 * (lo + hi))
    s = sum(result)
    return [p / s for p in result] if s > 0 else [r / total for r in raw]


def implied_probabilities(
    odds_h: float | None,
    odds_d: float | None,
    odds_a: float | None,
    method: str = "power",
) -> tuple[float, float, float] | None:
    """(1, 0, 2) oranlarından olasılık üçlüsü. Eksik/geçersiz oranda None."""
    values = [odds_h, odds_d, odds_a]
    if any(v is None or not isinstance(v, (int, float)) or v != v or v <= 1.0 for v in values):
        return None
    p = remove_margin([float(v) for v in values], method)
    return p[0], p[1], p[2]


def overround(odds_h: float, odds_d: float, odds_a: float) -> float:
    """Kitapçı marjı (0.06 = %6)."""
    return (1.0 / odds_h + 1.0 / odds_d + 1.0 / odds_a) - 1.0


def market_frame(df, method: str = "power", prefix: str = ""):
    """DataFrame'e `mkt_h/mkt_d/mkt_a` sütunlarını ekler.

    Kapanış oranı varsa onu tercih eder (piyasanın son ve en bilgili hâli),
    yoksa açılış/ortalama oranına düşer.
    """
    n = len(df)
    out_h = np.full(n, np.nan)
    out_d = np.full(n, np.nan)
    out_a = np.full(n, np.nan)
    has_close = {"codds_h", "codds_d", "codds_a"}.issubset(df.columns)

    oh = df["odds_h"].to_numpy() if "odds_h" in df.columns else np.full(n, np.nan)
    od = df["odds_d"].to_numpy() if "odds_d" in df.columns else np.full(n, np.nan)
    oa = df["odds_a"].to_numpy() if "odds_a" in df.columns else np.full(n, np.nan)
    ch = df["codds_h"].to_numpy() if has_close else np.full(n, np.nan)
    cd = df["codds_d"].to_numpy() if has_close else np.full(n, np.nan)
    ca = df["codds_a"].to_numpy() if has_close else np.full(n, np.nan)

    for i in range(n):
        probs = implied_probabilities(ch[i], cd[i], ca[i], method)
        if probs is None:
            probs = implied_probabilities(oh[i], od[i], oa[i], method)
        if probs is not None:
            out_h[i], out_d[i], out_a[i] = probs

    # Açılış oranından da olasılık üret: kapanışla farkı "oran hareketi"dir
    # ve paranın hangi yöne aktığını gösterir.
    open_h = np.full(n, np.nan)
    open_d = np.full(n, np.nan)
    open_a = np.full(n, np.nan)
    for i in range(n):
        probs = implied_probabilities(oh[i], od[i], oa[i], method)
        if probs is not None:
            open_h[i], open_d[i], open_a[i] = probs

    result = df.copy()
    result[f"{prefix}mkt_h"] = out_h
    result[f"{prefix}mkt_d"] = out_d
    result[f"{prefix}mkt_a"] = out_a
    result[f"{prefix}mkto_h"] = open_h
    result[f"{prefix}mkto_d"] = open_d
    result[f"{prefix}mkto_a"] = open_a
    return result


@dataclass
class DriftAdjustment:
    """Oran hareketini piyasa olasılığına katan tek parametreli düzeltme.

    Kapanış oranı zaten piyasanın en bilgili hâlidir; hareketin taşıdığı ek
    bilgi, kapanışın hareketi tam yansıtıp yansıtmadığıdır. Bunu tek bir
    katsayıyla modelleriz:

        p' ∝ p_kapanış · (p_kapanış / p_açılış)^γ

    γ > 0: piyasa hareketi yeterince ileri götürmemiş, yönü bir miktar
    uzatmak faydalı. γ = 0: hareketin ek bilgisi yok (kapanış zaten yeterli).
    γ doğrulama penceresinde log-loss ile kestirilir ve **tutulan veride**
    kazanç yoksa uygulanmaz.
    """

    gamma: float = 0.0
    fitted: bool = False
    gain: float = 0.0

    def fit(self, close, open_, outcomes) -> "DriftAdjustment":
        from scipy.optimize import minimize_scalar

        close = np.asarray(close, dtype=float)
        open_ = np.asarray(open_, dtype=float)
        y = np.asarray(outcomes, dtype=int)
        usable = ~(np.isnan(close).any(axis=1) | np.isnan(open_).any(axis=1)) & (y >= 0)
        if usable.sum() < 400:
            return self
        close, open_, y = close[usable], open_[usable], y[usable]

        split = int(len(y) * 0.7)

        def loss(gamma, c, o, target):
            p = self._apply(c, o, gamma)
            return -np.mean(np.log(np.clip(p[np.arange(len(target)), target], 1e-9, 1.0)))

        result = minimize_scalar(
            lambda g: loss(g, close[:split], open_[:split], y[:split]),
            bounds=(-1.5, 1.5), method="bounded",
        )
        gamma = float(result.x)
        baseline = loss(0.0, close[split:], open_[split:], y[split:])
        gain = baseline - loss(gamma, close[split:], open_[split:], y[split:])
        if gain <= 1e-4:
            return self
        self.gamma, self.fitted, self.gain = gamma, True, float(gain)
        return self

    @staticmethod
    def _apply(close, open_, gamma: float):
        close = np.clip(close, 1e-9, 1.0)
        open_ = np.clip(open_, 1e-9, 1.0)
        logits = np.log(close) + gamma * (np.log(close) - np.log(open_))
        logits -= logits.max(axis=1, keepdims=True)
        out = np.exp(logits)
        return out / out.sum(axis=1, keepdims=True)

    def apply(self, close, open_):
        close = np.asarray(close, dtype=float)
        if not self.fitted:
            return close
        open_ = np.asarray(open_, dtype=float)
        missing = np.isnan(open_).any(axis=1)
        out = self._apply(close, np.where(np.isnan(open_), close, open_), self.gamma)
        out[missing] = close[missing]     # hareket bilinmiyorsa kapanışı kullan
        return out

    def describe(self) -> str:
        if not self.fitted:
            return "oran hareketi: uygulanmıyor (kazanç yok)"
        return f"oran hareketi: γ={self.gamma:+.3f} | log-loss kazancı {self.gain:.4f}"

    def to_dict(self) -> dict:
        return {"gamma": self.gamma, "fitted": self.fitted, "gain": self.gain}

    @classmethod
    def from_dict(cls, data: dict) -> "DriftAdjustment":
        return cls(
            gamma=float(data.get("gamma", 0.0)),
            fitted=bool(data.get("fitted", False)),
            gain=float(data.get("gain", 0.0)),
        )
