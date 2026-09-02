"""Zaman ağırlıklı Dixon–Coles gol modeli.

Her takım için bir atak ve bir defans parametresi kestirilir:

    log λ_ev   = base + atak[ev]  - defans[dep] + γ
    log μ_dep  = base + atak[dep] - defans[ev]

Poisson bağımsızlığı düşük skorlarda (özellikle 0-0 ve 1-1) gerçek veriyle
uyuşmadığı için Dixon & Coles (1997) τ düzeltmesi uygulanır — beraberlik
olasılığını doğru vermek Spor Toto için kritiktir.

Geçmiş maçlar `w = exp(-ξ · gün)` ile ağırlıklandırılır: eski maçlar kadro
değişimi nedeniyle daha az bilgi taşır.

Optimizasyon L-BFGS-B ile, **analitik gradyanla** yapılır; sayısal gradyana
göre ~50 kat hızlı, bu da yürüyen backtest'te yüzlerce yeniden fiti mümkün kılar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

_TAU_FLOOR = 1e-6


@dataclass
class DixonColesFit:
    """Bir lig için kestirilmiş parametreler."""

    teams: list[str]
    attack: dict[str, float]
    defense: dict[str, float]
    base: float
    home_advantage: float
    rho: float
    n_matches: int
    effective_n: float
    log_likelihood: float
    converged: bool
    league: str | None = None
    team_matches: dict[str, float] = field(default_factory=dict)

    def rates(
        self,
        home: str,
        away: str,
        adjustments: dict[str, tuple[float, float]] | None = None,
    ) -> tuple[float, float]:
        """(λ_ev, μ_deplasman) gol beklentileri. Bilinmeyen takım lig ortalaması sayılır."""
        atk_h = self.attack.get(home, 0.0)
        atk_a = self.attack.get(away, 0.0)
        dfn_h = self.defense.get(home, 0.0)
        dfn_a = self.defense.get(away, 0.0)
        if adjustments:
            ah, dh = adjustments.get(home, (0.0, 0.0))
            aa, da = adjustments.get(away, (0.0, 0.0))
            atk_h += ah; dfn_h += dh
            atk_a += aa; dfn_a += da
        lam = math.exp(self.base + atk_h - dfn_a + self.home_advantage)
        mu = math.exp(self.base + atk_a - dfn_h)
        return lam, mu

    def knows(self, team: str) -> bool:
        return team in self.attack


def _tau(hg: np.ndarray, ag: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float):
    """Dixon–Coles düşük skor düzeltmesi ve λ, μ, ρ'ya göre türevleri."""
    tau = np.ones_like(lam)
    d_lam = np.zeros_like(lam)
    d_mu = np.zeros_like(lam)
    d_rho = np.zeros_like(lam)

    m00 = (hg == 0) & (ag == 0)
    m01 = (hg == 0) & (ag == 1)
    m10 = (hg == 1) & (ag == 0)
    m11 = (hg == 1) & (ag == 1)

    tau[m00] = 1.0 - lam[m00] * mu[m00] * rho
    d_lam[m00] = -mu[m00] * rho
    d_mu[m00] = -lam[m00] * rho
    d_rho[m00] = -lam[m00] * mu[m00]

    tau[m01] = 1.0 + lam[m01] * rho
    d_lam[m01] = rho
    d_rho[m01] = lam[m01]

    tau[m10] = 1.0 + mu[m10] * rho
    d_mu[m10] = rho
    d_rho[m10] = mu[m10]

    tau[m11] = 1.0 - rho
    d_rho[m11] = -1.0

    # τ ≤ 0 sayısal olarak geçersiz; taban uygula ve o noktada gradyanı kes.
    bad = tau < _TAU_FLOOR
    if bad.any():
        tau = np.where(bad, _TAU_FLOOR, tau)
        d_lam = np.where(bad, 0.0, d_lam)
        d_mu = np.where(bad, 0.0, d_mu)
        d_rho = np.where(bad, 0.0, d_rho)
    return tau, d_lam, d_mu, d_rho


class DixonColes:
    """Tek bir lig için Dixon–Coles kestirimi."""

    def __init__(self, xi: float = 0.0018, ridge: float = 0.02, max_goals: int = 12):
        self.xi = xi
        self.ridge = ridge
        self.max_goals = max_goals

    # -- kestirim ---------------------------------------------------------
    def build_objective(self, df, as_of=None):
        """Hedef fonksiyonu, başlangıç noktasını, sınırları ve çözücüyü kurar.

        `fit`'ten ayrı tutuldu ki analitik gradyan bağımsız olarak sayısal
        gradyanla karşılaştırılabilsin (bkz. tests/test_models.py). Yanlış bir
        gradyan optimizasyonu sessizce yanlış bir noktaya yakınsatır.
        """
        import pandas as pd

        data = df.dropna(subset=["fthg", "ftag"]).copy()
        if data.empty:
            raise ValueError("Dixon-Coles için oynanmış maç yok")

        as_of = pd.Timestamp(as_of) if as_of is not None else data["date"].max()
        age_days = (as_of - data["date"]).dt.days.to_numpy().astype(float)
        age_days = np.clip(age_days, 0.0, None)
        weights = np.exp(-self.xi * age_days)

        teams = sorted(set(data["home"]) | set(data["away"]))
        index = {t: i for i, t in enumerate(teams)}
        n_teams = len(teams)
        if n_teams < 2:
            raise ValueError("En az 2 takım gerekli")

        hi = data["home"].map(index).to_numpy()
        ai = data["away"].map(index).to_numpy()
        hg = data["fthg"].to_numpy().astype(int)
        ag = data["ftag"].to_numpy().astype(int)

        # Parametre vektörü: [atak(n-1) | defans(n-1) | base | γ | ρ]
        n_free = n_teams - 1
        x0 = np.zeros(2 * n_free + 3)
        x0[-3] = math.log(max(data[["fthg", "ftag"]].to_numpy().mean(), 0.2))  # base
        x0[-2] = 0.25   # ev avantajı
        x0[-1] = -0.05  # ρ

        bounds = (
            [(-3.0, 3.0)] * n_free
            + [(-3.0, 3.0)] * n_free
            + [(-2.0, 3.0), (-1.0, 1.5), (-0.25, 0.25)]
        )

        def unpack(x):
            atk = np.empty(n_teams)
            dfn = np.empty(n_teams)
            atk[:n_free] = x[:n_free]
            atk[n_free] = -x[:n_free].sum()
            dfn[:n_free] = x[n_free : 2 * n_free]
            dfn[n_free] = -x[n_free : 2 * n_free].sum()
            return atk, dfn, x[-3], x[-2], x[-1]

        def objective(x):
            atk, dfn, base, gamma, rho = unpack(x)
            log_lam = base + atk[hi] - dfn[ai] + gamma
            log_mu = base + atk[ai] - dfn[hi]
            lam = np.exp(np.clip(log_lam, -8, 3))
            mu = np.exp(np.clip(log_mu, -8, 3))

            tau, dt_lam, dt_mu, dt_rho = _tau(hg, ag, lam, mu, rho)
            ll = weights * (
                np.log(tau) + hg * log_lam - lam + ag * log_mu - mu
            )
            penalty = self.ridge * (np.sum(atk**2) + np.sum(dfn**2))
            total = -ll.sum() + penalty

            # --- gradyan ---
            # d/dθ [x·logλ - λ] = (x - λ)·dlogλ/dθ
            # d/dθ [log τ]      = (1/τ)(∂τ/∂λ·λ + ∂τ/∂μ·μ)·dlog·/dθ
            gl = weights * ((hg - lam) + lam * dt_lam / tau)
            gm = weights * ((ag - mu) + mu * dt_mu / tau)

            g_atk = np.bincount(hi, gl, n_teams) + np.bincount(ai, gm, n_teams)
            g_dfn = -np.bincount(ai, gl, n_teams) - np.bincount(hi, gm, n_teams)
            g_base = gl.sum() + gm.sum()
            g_gamma = gl.sum()
            g_rho = (weights * dt_rho / tau).sum()

            grad = np.empty_like(x)
            # Toplamı sıfır kısıtı: son takımın türevi diğerlerinden çıkarılır.
            # Ridge türevi de aynı kısıt üzerinden zincirlenir:
            #   d/dfree_i Σatk² = 2·(atk_i - atk_last)
            grad[:n_free] = -(g_atk[:n_free] - g_atk[n_free]) + 2 * self.ridge * (
                atk[:n_free] - atk[n_free]
            )
            grad[n_free : 2 * n_free] = -(g_dfn[:n_free] - g_dfn[n_free]) + 2 * self.ridge * (
                dfn[:n_free] - dfn[n_free]
            )
            grad[-3] = -g_base
            grad[-2] = -g_gamma
            grad[-1] = -g_rho
            return total, grad

        counts = np.bincount(hi, weights, n_teams) + np.bincount(ai, weights, n_teams)
        return {
            "objective": objective,
            "x0": x0,
            "bounds": bounds,
            "unpack": unpack,
            "teams": teams,
            "index": index,
            "n_matches": len(data),
            "weights": weights,
            "counts": counts,
        }

    def fit(self, df, as_of=None, league: str | None = None) -> DixonColesFit:
        """`df`: league/date/home/away/fthg/ftag sütunlu oynanmış maçlar."""
        problem = self.build_objective(df, as_of)
        result = minimize(
            problem["objective"], problem["x0"], jac=True, method="L-BFGS-B",
            bounds=problem["bounds"],
            options={"maxiter": 500, "ftol": 1e-10, "gtol": 1e-7},
        )
        atk, dfn, base, gamma, rho = problem["unpack"](result.x)
        index = problem["index"]
        counts = problem["counts"]
        return DixonColesFit(
            teams=problem["teams"],
            attack={t: float(atk[i]) for t, i in index.items()},
            defense={t: float(dfn[i]) for t, i in index.items()},
            base=float(base),
            home_advantage=float(gamma),
            rho=float(rho),
            n_matches=problem["n_matches"],
            effective_n=float(problem["weights"].sum()),
            log_likelihood=float(-result.fun),
            converged=bool(result.success),
            league=league,
            team_matches={t: float(counts[i]) for t, i in index.items()},
        )

    # -- tahmin -----------------------------------------------------------
    def score_matrix(self, lam: float, mu: float, rho: float) -> np.ndarray:
        """(max_goals+1)×(max_goals+1) skor olasılık matrisi."""
        k = np.arange(self.max_goals + 1)
        log_fact = np.cumsum(np.concatenate([[0.0], np.log(np.arange(1, self.max_goals + 1))]))
        ph = np.exp(-lam + k * math.log(max(lam, 1e-12)) - log_fact)
        pa = np.exp(-mu + k * math.log(max(mu, 1e-12)) - log_fact)
        matrix = np.outer(ph, pa)

        matrix[0, 0] *= 1.0 - lam * mu * rho
        matrix[0, 1] *= 1.0 + lam * rho
        matrix[1, 0] *= 1.0 + mu * rho
        matrix[1, 1] *= 1.0 - rho
        np.clip(matrix, 0.0, None, out=matrix)
        total = matrix.sum()
        return matrix / total if total > 0 else matrix

    def outcome_probabilities(self, fit: DixonColesFit, home: str, away: str,
                              adjustments=None) -> tuple[float, float, float]:
        """(P(1), P(0), P(2)) döner."""
        lam, mu = fit.rates(home, away, adjustments)
        matrix = self.score_matrix(lam, mu, fit.rho)
        p_home = float(np.tril(matrix, -1).sum())
        p_draw = float(np.trace(matrix))
        p_away = float(np.triu(matrix, 1).sum())
        total = p_home + p_draw + p_away
        return p_home / total, p_draw / total, p_away / total
