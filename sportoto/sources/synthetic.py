"""Sentetik lig üreticisi — testler ve çevrimdışı doğrulama için.

Bilinen "gerçek" takım güçlerinden Poisson skorları üretir. Böylece modelin
bu güçleri geri kazanıp kazanamadığı ölçülebilir; ayrıca ağ olmadan uçtan uca
boru hattı çalıştırılabilir. Üretim deterministiktir (sabit tohum).
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from .base import Source


def generate_league(
    n_teams: int = 18,
    n_seasons: int = 6,
    seed: int = 7,
    league: str = "SYN",
    start: date | None = None,
    home_advantage: float = 0.26,
    with_odds: bool = True,
    bookmaker_margin: float = 0.06,
) -> list[dict]:
    """Çift devreli lig fikstürü üretir ve Poisson skorlarıyla doldurur."""
    rng = random.Random(seed)
    start = start or date(2018, 8, 1)
    teams = [f"Team {chr(65 + i)}" for i in range(n_teams)]
    # Gerçek güçler: atak ve defans, sıfır toplamlı.
    attack = {t: rng.gauss(0, 0.30) for t in teams}
    defense = {t: rng.gauss(0, 0.25) for t in teams}
    for d in (attack, defense):
        mean = sum(d.values()) / len(d)
        for t in d:
            d[t] -= mean

    rows: list[dict] = []
    day = start
    for season_idx in range(n_seasons):
        season = f"{(start.year + season_idx) % 100:02d}{(start.year + season_idx + 1) % 100:02d}"
        # Sezon başında güçler biraz kayar (transfer/oyuncu değişimi).
        if season_idx:
            for t in teams:
                attack[t] += rng.gauss(0, 0.08)
                defense[t] += rng.gauss(0, 0.08)
        fixtures = [(h, a) for h in teams for a in teams if h != a]
        rng.shuffle(fixtures)
        for i, (home, away) in enumerate(fixtures):
            if i % (n_teams // 2) == 0:
                day += timedelta(days=7)
            lam_h = math.exp(0.10 + attack[home] - defense[away] + home_advantage)
            lam_a = math.exp(0.10 + attack[away] - defense[home])
            hg = _poisson(rng, lam_h)
            ag = _poisson(rng, lam_a)
            row = {
                "league": league,
                "season": season,
                "date": day.isoformat(),
                "home": home,
                "away": away,
                "fthg": hg,
                "ftag": ag,
                "ftr": "H" if hg > ag else ("A" if hg < ag else "D"),
                "source": "synthetic",
            }
            if with_odds:
                ph, pd_, pa = _true_probs(lam_h, lam_a)
                # Kitapçı gerçek olasılığa gürültü ve marj ekler.
                noisy = [max(1e-4, p * math.exp(rng.gauss(0, 0.06))) for p in (ph, pd_, pa)]
                total = sum(noisy)
                scale = (1.0 + bookmaker_margin) / total
                row["odds_h"] = round(1.0 / (noisy[0] * scale), 2)
                row["odds_d"] = round(1.0 / (noisy[1] * scale), 2)
                row["odds_a"] = round(1.0 / (noisy[2] * scale), 2)
            rows.append(row)
    return rows


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth yöntemiyle Poisson örneklemi."""
    limit = math.exp(-lam)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1
        if k > 25:
            return k


def _true_probs(lam_h: float, lam_a: float, max_goals: int = 12):
    ph = pd_ = pa = 0.0
    ph_pmf = [math.exp(-lam_h) * lam_h**k / math.factorial(k) for k in range(max_goals + 1)]
    pa_pmf = [math.exp(-lam_a) * lam_a**k / math.factorial(k) for k in range(max_goals + 1)]
    for i, pi in enumerate(ph_pmf):
        for j, pj in enumerate(pa_pmf):
            if i > j:
                ph += pi * pj
            elif i == j:
                pd_ += pi * pj
            else:
                pa += pi * pj
    total = ph + pd_ + pa
    return ph / total, pd_ / total, pa / total


class SyntheticSource(Source):
    name = "synthetic"
    provides_odds = True

    def fetch(self, leagues: list[str], seasons: list[str] | None = None) -> list[dict]:
        codes = leagues or ["SYN"]
        rows: list[dict] = []
        for idx, code in enumerate(codes):
            rows.extend(generate_league(seed=7 + idx, league=code.upper()))
        return rows
