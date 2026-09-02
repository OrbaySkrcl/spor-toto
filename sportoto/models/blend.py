"""Bileşen modelleri logaritmik görüş havuzunda (log-opinion pool) birleştirir.

    p ∝ Π_k p_k^{w_k},   Σw_k = 1

Neden aritmetik ortalama değil de log havuz:
  * Aritmetik ortalama, bir model "bu kesinlikle olmaz" derken bile
    olasılığı yüksek tutar; log havuz bu vetoyu korur.
  * Sonuç, bileşenlerin geometrik ortalamasıdır — kalibrasyonu daha iyi
    korur ve fazla-güvenli tahmin üretme eğilimi düşüktür.

Ağırlıklar sabit değil, doğrulama penceresinde **log-loss minimize edilerek**
kestirilir. Bir bileşen (ör. oran) eksikse ağırlıklar mevcutlar üzerinde
yeniden normalize edilir; böylece oransız lig/maçlarda sistem çalışmaya devam eder.

`temperature` parametresi son olasılığı yumuşatır/keskinleştirir; o da aynı
doğrulama penceresinde fit edilir ve modelin fazla-güvenli olmasını engeller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

log = logging.getLogger(__name__)

_EPS = 1e-9


@dataclass
class LogPoolBlend:
    components: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    temperature: float = 1.0
    fitted: bool = False
    fit_log_loss: float | None = None
    #: Bir bileşenin fit'e girebilmesi için gereken en az kapsama oranı.
    min_coverage: float = 0.05
    #: Sıcaklık sınırları. Serbest bırakılırsa dejenere kalibrasyon verisinde
    #: 0'a yaklaşıp olasılıkları 0/1'e iter; bu da kupon optimizasyonunu bozar.
    temp_min: float = 0.6
    temp_max: float = 1.8
    #: Kalibrasyonda her bileşenin kaç maçta kullanılabilir olduğu (0-1).
    coverage: dict[str, float] = field(default_factory=dict)

    def _weight_vector(self) -> np.ndarray:
        return np.array([self.weights.get(c, 0.0) for c in self.components])

    @staticmethod
    def _pool(stack: np.ndarray, mask: np.ndarray, w: np.ndarray, temperature: float) -> np.ndarray:
        """stack: (K, N, 3) bileşen olasılıkları, mask: (K, N) bileşen mevcut mu."""
        log_p = np.log(np.clip(stack, _EPS, 1.0))
        wm = w[:, None] * mask                       # (K, N)
        norm = wm.sum(axis=0)                        # (N,)
        # Hiçbir bileşen yoksa düzgün dağılıma düş.
        safe = np.where(norm > _EPS, norm, 1.0)
        combined = (wm[:, :, None] * log_p).sum(axis=0) / safe[:, None]
        combined = np.where(norm[:, None] > _EPS, combined, np.log(1.0 / 3.0))
        combined /= max(temperature, 1e-3)
        combined -= combined.max(axis=1, keepdims=True)
        p = np.exp(combined)
        return p / p.sum(axis=1, keepdims=True)

    def _temperature_from(self, raw: float) -> float:
        """Serbest parametreyi [temp_min, temp_max] aralığına sıkıştırır."""
        span = self.temp_max - self.temp_min
        return self.temp_min + span * float(0.5 * (1.0 + np.tanh(0.5 * raw)))

    def fit(self, component_probs: dict[str, np.ndarray], outcomes: np.ndarray,
            sample_weights: np.ndarray | None = None) -> "LogPoolBlend":
        """`component_probs`: ad -> (N,3) dizi (NaN = o maç için bileşen yok).

        `outcomes`: 0 = ev (1), 1 = beraberlik (0), 2 = deplasman (2).
        """
        candidates = [k for k, v in component_probs.items() if v is not None and len(v)]
        if not candidates:
            raise ValueError("Birleştirilecek bileşen yok")

        # Kapsama denetimi: kalibrasyon verisinde neredeyse hiç görünmeyen bir
        # bileşene ağırlık atamak uydurmadır — optimizasyon o ağırlığı serbest
        # bırakır (maskelendiği için log-loss'u değiştirmez) ve sonra üretimde
        # o bileşen geldiğinde doğrulanmamış bir ağırlıkla devreye girer.
        # Bu yüzden kapsaması düşük bileşenler fit'ten tamamen çıkarılır.
        all_stack = np.stack([np.asarray(component_probs[n], dtype=float) for n in candidates])
        all_mask = ~np.isnan(all_stack).any(axis=2)
        self.coverage = {n: float(all_mask[i].mean()) for i, n in enumerate(candidates)}

        names = [n for n in candidates if self.coverage[n] >= self.min_coverage]
        dropped = [n for n in candidates if n not in names]
        if dropped:
            log.warning(
                "Kapsaması yetersiz bileşenler blend dışı bırakıldı: %s",
                ", ".join(f"{n} ({self.coverage[n]:.1%})" for n in dropped),
            )
        if not names:
            raise ValueError("Yeterli kapsamaya sahip bileşen yok")
        self.components = names

        keep = [candidates.index(n) for n in names]
        stack = all_stack[keep]
        mask = all_mask[keep]
        stack = np.nan_to_num(stack, nan=1.0 / 3.0)
        y = np.asarray(outcomes, dtype=int)
        sw = np.ones(len(y)) if sample_weights is None else np.asarray(sample_weights, float)
        sw = sw / sw.sum()

        # Hiç kullanılabilir bileşeni olmayan maçları fit dışında bırak.
        usable = mask.any(axis=0)
        if usable.sum() < 30:
            self.weights = {n: 1.0 / len(names) for n in names}  # eşit ağırlığa düş
            self.fitted = False
            return self
        stack, mask, y, sw = stack[:, usable], mask[:, usable], y[usable], sw[usable]
        sw = sw / sw.sum()

        k = len(names)
        idx = np.arange(len(y))

        def nll(params):
            # Softmax parametrelemesi: ağırlıklar otomatik olarak ≥0 ve toplamı 1.
            raw = np.concatenate([[0.0], params[: k - 1]])
            w = np.exp(raw - raw.max())
            w /= w.sum()
            temperature = self._temperature_from(params[k - 1])
            p = self._pool(stack, mask, w, temperature)
            return -np.sum(sw * np.log(np.clip(p[idx, y], _EPS, 1.0)))

        x0 = np.zeros(k)
        # Sıcaklık için 1.0'a karşılık gelen ham değerden başla (nötr).
        target = np.clip((1.0 - self.temp_min) / max(self.temp_max - self.temp_min, 1e-9), 1e-3, 1 - 1e-3)
        x0[k - 1] = 2.0 * np.arctanh(2.0 * target - 1.0)
        result = minimize(nll, x0, method="Nelder-Mead",
                          options={"maxiter": 3000, "xatol": 1e-5, "fatol": 1e-9})
        raw = np.concatenate([[0.0], result.x[: k - 1]])
        w = np.exp(raw - raw.max())
        w /= w.sum()
        self.weights = {n: float(wi) for n, wi in zip(names, w)}
        self.temperature = float(self._temperature_from(result.x[k - 1]))
        self.fit_log_loss = float(result.fun)
        self.fitted = bool(result.success)
        return self

    def predict(self, component_probs: dict[str, np.ndarray]) -> np.ndarray:
        if not self.components:
            raise RuntimeError("Blend henüz fit edilmedi")
        arrays = []
        for name in self.components:
            value = component_probs.get(name)
            if value is None:
                length = next(
                    len(v) for v in component_probs.values() if v is not None
                )
                value = np.full((length, 3), np.nan)
            arrays.append(np.asarray(value, dtype=float))
        stack = np.stack(arrays)
        mask = ~np.isnan(stack).any(axis=2)
        stack = np.nan_to_num(stack, nan=1.0 / 3.0)
        return self._pool(stack, mask, self._weight_vector(), self.temperature)

    def describe(self) -> str:
        parts = []
        for n in self.components:
            cov = self.coverage.get(n)
            suffix = f" (kapsama {cov:.0%})" if cov is not None and cov < 0.999 else ""
            parts.append(f"{n}={self.weights.get(n, 0):.2f}{suffix}")
        return f"ağırlıklar: {', '.join(parts)} | sıcaklık={self.temperature:.3f}"

    def to_dict(self) -> dict:
        return {
            "components": self.components,
            "weights": self.weights,
            "temperature": self.temperature,
            "fitted": self.fitted,
            "fit_log_loss": self.fit_log_loss,
            "coverage": self.coverage,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LogPoolBlend":
        return cls(
            components=list(data.get("components", [])),
            weights=dict(data.get("weights", {})),
            temperature=float(data.get("temperature", 1.0)),
            fitted=bool(data.get("fitted", False)),
            fit_log_loss=data.get("fit_log_loss"),
            coverage=dict(data.get("coverage", {})),
        )
