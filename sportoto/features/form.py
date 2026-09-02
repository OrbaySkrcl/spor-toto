"""Maç öncesi form, fikstür yoğunluğu ve ikili rekabet özellikleri.

Tüm özellikler **yalnızca maçtan önceki** maçlardan üretilir; tek geçişte,
takım başına kayan pencerelerle hesaplanır. Bu, backtest'te geriye bakma
(look-ahead) sızıntısını yapısal olarak imkânsız kılar.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque

FORM_COLUMNS = [
    "rest_home", "rest_away", "rest_diff",
    "load14_home", "load14_away",
    "form5_pts_home", "form5_pts_away", "form5_pts_diff",
    "form5_gd_home", "form5_gd_away", "form5_gd_diff",
    "venue_form_home", "venue_form_away", "venue_form_diff",
    "shot_dom_home", "shot_dom_away", "shot_dom_diff",
    "h2h_score", "h2h_weight",
]

#: H2H yarı ömrü (gün). 2 yıl önceki karşılaşma bugünkününün 1/4'ü ağırlıkta.
_H2H_HALFLIFE_DAYS = 365.0


def rest_days(last_played: dict, team: str, current) -> float:
    """Son maçtan bu yana geçen gün. Bilinmiyorsa 7 (nötr) varsayılır."""
    prev = last_played.get(team)
    if prev is None:
        return 7.0
    return min((current - prev).days, 30.0)


def head_to_head(history: dict, home: str, away: str, current) -> tuple[float, float]:
    """Zaman ağırlıklı H2H skoru ve toplam ağırlık.

    Skor ev sahibi bakış açısından -1 (hep kaybetti) ile +1 (hep kazandı) arası.
    Ağırlık, kaç maçlık kanıta dayandığını gösterir; blend bunu kullanarak
    az kanıtlı H2H'ye az güvenir.
    """
    key = tuple(sorted((home, away)))
    records = history.get(key)
    if not records:
        return 0.0, 0.0
    total_w = 0.0
    score = 0.0
    for played_on, winner in records:
        age = (current - played_on).days
        if age < 0:
            continue
        w = 0.5 ** (age / _H2H_HALFLIFE_DAYS)
        total_w += w
        if winner is None:
            continue
        score += w * (1.0 if winner == home else -1.0)
    return (score / total_w if total_w > 0 else 0.0), total_w


def build_form_features(df):
    """`df`'e FORM_COLUMNS sütunlarını ekleyip döner. `df` tarihe göre sıralı olmalı."""
    import numpy as np
    import pandas as pd

    # Kararlı sıralama — gerekçesi için `features/elo.py` içindeki nota bakın.
    df = df.sort_values(
        ["date", "match_id"] if "match_id" in df.columns else ["date"], kind="mergesort"
    ).reset_index(drop=True)
    n = len(df)

    last_played: dict[str, object] = {}
    recent_dates: dict[str, deque] = defaultdict(lambda: deque(maxlen=12))
    form_all: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))   # (puan, gol farkı)
    form_venue: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))  # aynı sahada puan
    shots: dict[str, deque] = defaultdict(lambda: deque(maxlen=5))       # isabetli şut farkı
    h2h: dict[tuple, list] = defaultdict(list)

    cols = {c: np.full(n, np.nan) for c in FORM_COLUMNS}

    dates = df["date"].to_numpy()
    homes = df["home"].to_numpy()
    aways = df["away"].to_numpy()
    hgs = df["fthg"].to_numpy()
    ags = df["ftag"].to_numpy()
    hsts = df["hst"].to_numpy() if "hst" in df.columns else np.full(n, np.nan)
    asts = df["ast"].to_numpy() if "ast" in df.columns else np.full(n, np.nan)

    for i in range(n):
        day = pd.Timestamp(dates[i])
        home, away = homes[i], aways[i]

        cols["rest_home"][i] = rest_days(last_played, home, day)
        cols["rest_away"][i] = rest_days(last_played, away, day)
        cols["rest_diff"][i] = cols["rest_home"][i] - cols["rest_away"][i]

        cols["load14_home"][i] = sum(1 for d in recent_dates[home] if (day - d).days <= 14)
        cols["load14_away"][i] = sum(1 for d in recent_dates[away] if (day - d).days <= 14)

        for team, side in ((home, "home"), (away, "away")):
            hist = form_all[team]
            cols[f"form5_pts_{side}"][i] = (
                sum(p for p, _ in hist) / len(hist) if hist else 1.35
            )
            cols[f"form5_gd_{side}"][i] = sum(g for _, g in hist) / len(hist) if hist else 0.0
            venue = form_venue[(team, side)]
            cols[f"venue_form_{side}"][i] = (
                sum(venue) / len(venue) if venue else (1.55 if side == "home" else 1.15)
            )
            sh = shots[team]
            cols[f"shot_dom_{side}"][i] = sum(sh) / len(sh) if sh else 0.0

        cols["form5_pts_diff"][i] = cols["form5_pts_home"][i] - cols["form5_pts_away"][i]
        cols["form5_gd_diff"][i] = cols["form5_gd_home"][i] - cols["form5_gd_away"][i]
        cols["venue_form_diff"][i] = cols["venue_form_home"][i] - cols["venue_form_away"][i]
        cols["shot_dom_diff"][i] = cols["shot_dom_home"][i] - cols["shot_dom_away"][i]

        score, weight = head_to_head(h2h, home, away, day)
        cols["h2h_score"][i] = score
        cols["h2h_weight"][i] = weight

        # --- durumu güncelle (bu maç artık geçmiş) ---
        hg, ag = hgs[i], ags[i]
        if pd.isna(hg) or pd.isna(ag):
            continue
        hg, ag = int(hg), int(ag)
        hp, ap = (3, 0) if hg > ag else ((0, 3) if hg < ag else (1, 1))
        form_all[home].append((hp, hg - ag))
        form_all[away].append((ap, ag - hg))
        form_venue[(home, "home")].append(hp)
        form_venue[(away, "away")].append(ap)
        if not (pd.isna(hsts[i]) or pd.isna(asts[i])):
            shots[home].append(float(hsts[i]) - float(asts[i]))
            shots[away].append(float(asts[i]) - float(hsts[i]))
        last_played[home] = last_played[away] = day
        recent_dates[home].append(day)
        recent_dates[away].append(day)
        winner = home if hg > ag else (away if ag > hg else None)
        h2h[tuple(sorted((home, away)))].append((day, winner))

    out = df.copy()
    for name, values in cols.items():
        out[name] = values
    return out


def motivation_proxy(standings: dict, team: str, matchday_fraction: float) -> float:
    """Sezonun son çeyreğinde sıralamadan türetilen kaba motivasyon göstergesi.

    Ücretsiz kaynakta "şampiyonluk yarışı / küme düşme" etiketi yok; bunu puan
    durumundan türetiyoruz. Sezonun ilk yarısında etkisi sıfır kabul edilir.
    """
    if matchday_fraction < 0.7 or team not in standings:
        return 0.0
    pos, total = standings[team]
    if total <= 1:
        return 0.0
    rel = (pos - 1) / (total - 1)  # 0 = lider, 1 = son
    # Uçlar (şampiyonluk / küme hattı) motive, orta sıralar değil.
    edge = max(0.0, 1.0 - abs(rel - 0.5) * 2.0)
    return (1.0 - edge) * (matchday_fraction - 0.7) / 0.3 * math.copysign(1.0, 0.5 - rel)
