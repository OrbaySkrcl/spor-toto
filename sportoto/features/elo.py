"""Gol farkı çarpanlı Elo derecelendirmesi.

Klasik Elo'dan iki farkı var:
  * Güncelleme gol farkına göre ölçekleniyor (5-0 kazanmak 1-0'dan fazla bilgi taşır).
  * Sezon başlarında dereceler lig ortalamasına doğru çekiliyor (kadro değişimi).

Elo, Dixon-Coles'tan farklı bir hata yapısına sahip olduğu için ensemble'a
bağımsız katkı verir: DC gol üretimini modeller, Elo sonuç dizisini.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class EloConfig:
    k: float = 20.0
    home_advantage: float = 60.0
    start: float = 1500.0
    season_regression: float = 0.25
    #: Lige yeni çıkan takımın lig ortalamasına göre başlangıç dezavantajı.
    promoted_penalty: float = 55.0


@dataclass
class EloRatings:
    """Maç akışını sırayla işleyerek derece geçmişi tutar."""

    config: EloConfig = field(default_factory=EloConfig)
    ratings: dict[str, float] = field(default_factory=dict)
    #: takım -> son işlenen sezon (regresyonu bir kez uygulamak için)
    _team_season: dict[str, str] = field(default_factory=dict)
    _league_of: dict[str, str] = field(default_factory=dict)
    matches_seen: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def _league_mean(self, league: str) -> float:
        vals = [r for t, r in self.ratings.items() if self._league_of.get(t) == league]
        return sum(vals) / len(vals) if vals else self.config.start

    def get(self, team: str, league: str | None = None) -> float:
        if team not in self.ratings:
            base = self._league_mean(league) if league else self.config.start
            # Lige yeni gelen takım ortalamanın altında başlar.
            self.ratings[team] = base - (self.config.promoted_penalty if league else 0.0)
            if league:
                self._league_of[team] = league
        return self.ratings[team]

    def _apply_season_regression(self, team: str, league: str, season: str | None) -> None:
        if not season:
            return
        if self._team_season.get(team) == season:
            return
        if team in self.ratings and self._team_season.get(team) is not None:
            mean = self._league_mean(league)
            r = self.config.season_regression
            self.ratings[team] = mean + (1.0 - r) * (self.ratings[team] - mean)
        self._team_season[team] = season

    def expected_home(self, home: str, away: str, league: str | None = None) -> float:
        """Ev sahibinin beklenen skoru (galibiyet=1, beraberlik=0.5)."""
        diff = self.get(home, league) + self.config.home_advantage - self.get(away, league)
        return 1.0 / (1.0 + 10.0 ** (-diff / 400.0))

    def rating_diff(self, home: str, away: str, league: str | None = None) -> float:
        """Ev avantajı dahil derece farkı — ordinal model bunu girdi alır."""
        return self.get(home, league) + self.config.home_advantage - self.get(away, league)

    def update(
        self,
        home: str,
        away: str,
        hg: int,
        ag: int,
        league: str | None = None,
        season: str | None = None,
    ) -> tuple[float, float]:
        """Bir maçı işler. Maç ÖNCESİ derece farkını ve beklentiyi döner."""
        if league:
            self._league_of.setdefault(home, league)
            self._league_of.setdefault(away, league)
            self._apply_season_regression(home, league, season)
            self._apply_season_regression(away, league, season)

        pre_diff = self.rating_diff(home, away, league)
        expected = 1.0 / (1.0 + 10.0 ** (-pre_diff / 400.0))
        actual = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)

        gd = abs(hg - ag)
        # Kazananın derece üstünlüğü arttıkça çarpan küçülür (şişmeyi önler).
        winner_diff = pre_diff if hg > ag else (-pre_diff if ag > hg else 0.0)
        mult = math.log(gd + 1.0) * (2.2 / (0.001 * winner_diff + 2.2)) if gd else 1.0
        delta = self.config.k * mult * (actual - expected)

        self.ratings[home] = self.get(home, league) + delta
        self.ratings[away] = self.get(away, league) - delta
        self.matches_seen[home] += 1
        self.matches_seen[away] += 1
        return pre_diff, expected


def build_elo_history(df, config: EloConfig | None = None):
    """Maç tablosunu tarihe göre işleyip her maça maç-öncesi Elo değerleri ekler.

    Sızıntı yok: bir maçın özellikleri yalnızca kendisinden önceki maçlardan üretilir.
    Döndürülen DataFrame `elo_home`, `elo_away`, `elo_diff`, `elo_exp_home` sütunlarını içerir.
    """
    import numpy as np
    import pandas as pd

    ratings = EloRatings(config or EloConfig())
    df = df.sort_values(["date", "match_id"] if "match_id" in df.columns else ["date"]).reset_index(
        drop=True
    )

    n = len(df)
    elo_h = np.empty(n)
    elo_a = np.empty(n)
    elo_d = np.empty(n)
    elo_e = np.empty(n)
    seen_h = np.empty(n, dtype=int)
    seen_a = np.empty(n, dtype=int)

    homes = df["home"].to_numpy()
    aways = df["away"].to_numpy()
    leagues = df["league"].to_numpy() if "league" in df.columns else [None] * n
    seasons = df["season"].to_numpy() if "season" in df.columns else [None] * n
    hgs = df["fthg"].to_numpy()
    ags = df["ftag"].to_numpy()

    for i in range(n):
        league = leagues[i]
        season = seasons[i] if seasons[i] is not None and str(seasons[i]) != "nan" else None
        # Sezon regresyonunu maç öncesi uygula ki okunan değerler güncel olsun.
        if league:
            ratings._league_of.setdefault(homes[i], league)
            ratings._league_of.setdefault(aways[i], league)
            ratings._apply_season_regression(homes[i], league, season)
            ratings._apply_season_regression(aways[i], league, season)

        elo_h[i] = ratings.get(homes[i], league)
        elo_a[i] = ratings.get(aways[i], league)
        elo_d[i] = ratings.rating_diff(homes[i], aways[i], league)
        elo_e[i] = 1.0 / (1.0 + 10.0 ** (-elo_d[i] / 400.0))
        seen_h[i] = ratings.matches_seen[homes[i]]
        seen_a[i] = ratings.matches_seen[aways[i]]

        hg, ag = hgs[i], ags[i]
        if pd.notna(hg) and pd.notna(ag):
            ratings.update(homes[i], aways[i], int(hg), int(ag), league, season)

    out = df.copy()
    out["elo_home"] = elo_h
    out["elo_away"] = elo_a
    out["elo_diff"] = elo_d
    out["elo_exp_home"] = elo_e
    out["elo_seen_home"] = seen_h
    out["elo_seen_away"] = seen_a
    return out, ratings
