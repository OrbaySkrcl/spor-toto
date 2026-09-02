"""Form / fikstür / H2H özelliklerinden 1-0-2 olasılığı (softmax regresyon).

Bu bileşen kasıtlı olarak **Elo ve oran görmez**. Amacı ensemble'a bağımsız
bir bakış açısı katmak: son maçların momentumu, iç-saha/deplasman ayrımı,
dinlenme günü, fikstür yoğunluğu ve zaman ağırlıklı ikili rekabet.

Tek başına zayıf bir modeldir; katkısı olup olmadığına blend ağırlıkları
karar verir (katkı yoksa ağırlığı kendiliğinden sıfıra iner).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

FEATURES = [
    "form5_pts_diff",
    "form5_gd_diff",
    "venue_form_diff",
    "shot_dom_diff",
    "rest_diff",
    "load_diff",
    "h2h_signal",
]

_EPS = 1e-9


def design_matrix(df) -> np.ndarray:
    """Form sütunlarından özellik matrisi kurar (ölçeklenmiş, NaN'siz)."""
    n = len(df)

    def col(name, default=0.0):
        if name not in df.columns:
            return np.full(n, default)
        return np.nan_to_num(df[name].to_numpy(dtype=float), nan=default)

    load_diff = col("load14_home") - col("load14_away")
    # H2H'yi kanıt miktarıyla söndür: 2 maçlık geçmiş 10 maçlık kadar sayılmasın.
    h2h_weight = col("h2h_weight")
    h2h_signal = col("h2h_score") * (h2h_weight / (h2h_weight + 2.0))

    return np.column_stack([
        col("form5_pts_diff") / 1.5,
        col("form5_gd_diff") / 1.5,
        col("venue_form_diff") / 1.5,
        col("shot_dom_diff") / 3.0,
        col("rest_diff") / 7.0,
        load_diff / 2.0,
        h2h_signal,
    ])


@dataclass
class FormModel:
    """Softmax (çok sınıflı lojistik) regresyon, L2 düzenlileştirmeli."""

    l2: float = 1.0
    coef: np.ndarray | None = None      # (n_features, 3)
    intercept: np.ndarray = field(default_factory=lambda: np.zeros(3))
    fitted: bool = False

    def fit(self, x: np.ndarray, y: np.ndarray, weights=None) -> "FormModel":
        """`y`: 0 = ev (1), 1 = beraberlik (0), 2 = deplasman (2)."""
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=int)
        n, d = x.shape
        if n < 200:
            return self
        w = np.ones(n) if weights is None else np.asarray(weights, dtype=float)
        w = w / w.sum()
        onehot = np.zeros((n, 3))
        onehot[np.arange(n), y] = 1.0

        def unpack(params):
            return params[: d * 3].reshape(d, 3), params[d * 3 :]

        def objective(params):
            coef, intercept = unpack(params)
            # Referans sınıf sabitlenmediği için L2 kimliği belirler.
            logits = x @ coef + intercept
            logits -= logits.max(axis=1, keepdims=True)
            exp = np.exp(logits)
            probs = exp / exp.sum(axis=1, keepdims=True)
            loss = -np.sum(w * np.log(np.clip(probs[np.arange(n), y], _EPS, 1.0)))
            loss += self.l2 * (np.sum(coef**2) + 0.01 * np.sum(intercept**2))

            resid = (probs - onehot) * w[:, None]
            g_coef = x.T @ resid + 2.0 * self.l2 * coef
            g_int = resid.sum(axis=0) + 2.0 * self.l2 * 0.01 * intercept
            return loss, np.concatenate([g_coef.ravel(), g_int])

        x0 = np.zeros(d * 3 + 3)
        result = minimize(objective, x0, jac=True, method="L-BFGS-B",
                          options={"maxiter": 400, "ftol": 1e-12})
        self.coef, self.intercept = unpack(result.x)
        self.fitted = bool(result.success)
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        if self.coef is None:
            return np.full((len(x), 3), 1.0 / 3.0)
        logits = x @ self.coef + self.intercept
        logits -= logits.max(axis=1, keepdims=True)
        exp = np.exp(logits)
        return exp / exp.sum(axis=1, keepdims=True)
