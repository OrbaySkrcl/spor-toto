"""Elo farkından 1/0/2 olasılığı üreten sıralı (ordinal) lojistik model.

Sonuçlar doğal olarak sıralıdır: deplasman galibiyeti < beraberlik < ev
galibiyeti. Sıralı logit bunu üç ayrı sınıf yerine tek bir gizli değişken +
iki eşik ile modeller; üç parametreyle idare ettiği için az veriyle bile
kararlıdır ve beraberlik olasılığını yapısal olarak "ortada" tutar.

    z = β · (elo_farkı / 100)
    P(2) = σ(θ₂ − z)
    P(0) = σ(θ₀ − z) − σ(θ₂ − z)
    P(1) = 1 − σ(θ₀ − z)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

_EPS = 1e-9


def _sigmoid(x):
    return 0.5 * (1.0 + np.tanh(0.5 * x))


@dataclass
class EloOrdinal:
    beta: float = 0.35
    theta_away: float = -0.55   # θ₂
    theta_draw: float = 0.55    # θ₀
    fitted: bool = False

    def fit(self, elo_diff, outcomes, weights=None) -> "EloOrdinal":
        """`outcomes`: 0 = ev (1), 1 = beraberlik (0), 2 = deplasman (2).

        Sınıf sırası `predict` ile birebir aynıdır; ikisi ayrışırsa model
        sessizce ters olasılık üretir (β işareti dönerek uyum sağlar).
        """
        x = np.asarray(elo_diff, dtype=float) / 100.0
        y = np.asarray(outcomes, dtype=int)
        w = np.ones_like(x) if weights is None else np.asarray(weights, dtype=float)
        if len(x) < 50:
            return self

        def nll(params):
            beta, theta_a, gap = params
            theta_d = theta_a + np.exp(gap)  # θ₀ > θ₂ garanti
            z = beta * x
            s_a = _sigmoid(theta_a - z)
            s_d = _sigmoid(theta_d - z)
            p = np.empty((len(x), 3))
            p[:, 0] = 1.0 - s_d      # P(1) ev
            p[:, 1] = s_d - s_a      # P(0) beraberlik
            p[:, 2] = s_a            # P(2) deplasman
            p = np.clip(p, _EPS, 1.0)
            return -np.sum(w * np.log(p[np.arange(len(y)), y]))

        result = minimize(
            nll,
            np.array([self.beta, self.theta_away, np.log(max(self.theta_draw - self.theta_away, 0.1))]),
            method="Nelder-Mead",
            options={"maxiter": 2000, "xatol": 1e-6, "fatol": 1e-8},
        )
        beta, theta_a, gap = result.x
        self.beta = float(beta)
        self.theta_away = float(theta_a)
        self.theta_draw = float(theta_a + np.exp(gap))
        self.fitted = bool(result.success)
        return self

    def predict(self, elo_diff) -> np.ndarray:
        """Nx3 olasılık matrisi, sütun sırası (1, 0, 2)."""
        x = np.atleast_1d(np.asarray(elo_diff, dtype=float)) / 100.0
        z = self.beta * x
        s_a = _sigmoid(self.theta_away - z)
        s_d = _sigmoid(self.theta_draw - z)
        p_away = s_a
        p_draw = np.clip(s_d - s_a, _EPS, None)
        p_home = 1.0 - s_d
        stacked = np.clip(np.stack([p_home, p_draw, p_away], axis=1), _EPS, None)
        return stacked / stacked.sum(axis=1, keepdims=True)

    def predict_one(self, elo_diff: float) -> tuple[float, float, float]:
        row = self.predict([elo_diff])[0]
        return float(row[0]), float(row[1]), float(row[2])
