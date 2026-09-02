"""Blend sonrası sınıf bazlı kalibrasyon (vektör ölçekleme).

Neden gerekli
-------------
Log havuzu bileşenlerin *göreli* ağırlığını öğrenir ama sınıfların sistematik
kaymasını düzeltmez. Futbolda bu kayma en çok **beraberlikte** görülür:
beraberlik hiçbir zaman favori olmadığı için modeller onu kronik olarak eksik
ya da fazla tahmin edebilir ve bu, doğrudan Spor Toto kuponuna yansır — çünkü
"0" işaretlemek çoğu maçta ikinci tercihtir.

Yöntem
------
Vektör ölçekleme: olasılıkların logaritmasına sınıf başına bir ölçek ve bir
sapma uygulanır, sonuç yeniden normalize edilir.

    p'_c ∝ exp(w_c · log p_c + b_c)

Yalnızca 6 parametre (3 ölçek + 3 sapma, biri sabitlenerek 5 serbest) olduğu
için az veriyle bile kararlıdır ve doğrulama penceresinde log-loss minimize
edilerek kestirilir. Kayma yoksa fit kimlik dönüşümüne yakınsar; yani zarar
vermez, yalnızca varsa düzeltir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

log = logging.getLogger(__name__)

_EPS = 1e-9


@dataclass
class VectorScaling:
    #: Sınıf başına ölçek (1 = değişiklik yok).
    scale: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0])
    #: Sınıf başına sapma (0 = değişiklik yok).
    bias: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    fitted: bool = False
    #: Kalibrasyonun doğrulama penceresinde sağladığı log-loss iyileşmesi.
    gain: float = 0.0

    def fit(self, probs: np.ndarray, outcomes: np.ndarray,
            min_rows: int = 300) -> "VectorScaling":
        probs = np.clip(np.asarray(probs, dtype=float), _EPS, 1.0)
        y = np.asarray(outcomes, dtype=int)
        if len(y) < min_rows:
            return self

        def unpack(params):
            scale = np.concatenate([[1.0], params[:2]])
            bias = np.concatenate([[0.0], params[2:]])
            return scale, bias

        def transform(source, params):
            scale, bias = unpack(params)
            logits = np.log(source) * scale + bias
            logits -= logits.max(axis=1, keepdims=True)
            out = np.exp(logits)
            return out / out.sum(axis=1, keepdims=True)

        def loss(source, target, params):
            p = transform(source, params)
            return -np.mean(np.log(np.clip(p[np.arange(len(target)), target], _EPS, 1.0)))

        # Kazancı, fit edilen veride değil tutulan veride ölç. Dört serbest
        # parametre gürültüden bile küçük bir "iyileşme" uydurabilir; tutulan
        # dilimde kazanç yoksa kalibrasyon uygulanmaz.
        split = int(len(y) * 0.7)
        fit_p, fit_y = probs[:split], y[:split]
        hold_p, hold_y = probs[split:], y[split:]

        x0 = np.array([1.0, 1.0, 0.0, 0.0])
        result = minimize(
            lambda params: loss(fit_p, fit_y, params), x0, method="Nelder-Mead",
            options={"maxiter": 3000, "xatol": 1e-6, "fatol": 1e-10},
        )
        if not np.all(np.isfinite(result.x)):
            return self

        rows = np.arange(len(hold_y))
        baseline = -np.mean(np.log(np.clip(hold_p[rows, hold_y], _EPS, 1.0)))
        gain = baseline - loss(hold_p, hold_y, result.x)
        if gain <= 1e-4:
            log.info("Sınıf kalibrasyonu tutulan veride kazanç vermedi (%.5f); atlanıyor", gain)
            return self

        # Kabul edildiyse tüm pencereyle yeniden kestir.
        final = minimize(
            lambda params: loss(probs, y, params), result.x, method="Nelder-Mead",
            options={"maxiter": 3000, "xatol": 1e-6, "fatol": 1e-10},
        )
        if not np.all(np.isfinite(final.x)):
            return self
        scale, bias = unpack(final.x)
        self.scale = [float(v) for v in scale]
        self.bias = [float(v) for v in bias]
        self.fitted = True
        self.gain = gain
        return self

    def apply(self, probs: np.ndarray) -> np.ndarray:
        probs = np.clip(np.asarray(probs, dtype=float), _EPS, 1.0)
        if not self.fitted:
            return probs / probs.sum(axis=1, keepdims=True)
        logits = np.log(probs) * np.array(self.scale) + np.array(self.bias)
        logits -= logits.max(axis=1, keepdims=True)
        out = np.exp(logits)
        return out / out.sum(axis=1, keepdims=True)

    def describe(self) -> str:
        if not self.fitted:
            return "sınıf kalibrasyonu: uygulanmıyor (kazanç yok)"
        labels = ("1", "0", "2")
        parts = ", ".join(
            f"{labels[i]}: ölçek {self.scale[i]:.2f} sapma {self.bias[i]:+.2f}"
            for i in range(3)
        )
        return f"sınıf kalibrasyonu: {parts} | log-loss kazancı {self.gain:.4f}"

    def to_dict(self) -> dict:
        return {
            "scale": self.scale, "bias": self.bias,
            "fitted": self.fitted, "gain": self.gain,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "VectorScaling":
        return cls(
            scale=list(data.get("scale", [1.0, 1.0, 1.0])),
            bias=list(data.get("bias", [0.0, 0.0, 0.0])),
            fitted=bool(data.get("fitted", False)),
            gain=float(data.get("gain", 0.0)),
        )
