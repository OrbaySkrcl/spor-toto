"""GitHub aynası — `datasets/football-datasets` deposu.

football-data.co.uk verisinin günlük güncellenen bir kopyası, ama **oran
sütunları yok** ve yalnızca 5 büyük Avrupa ligini kapsıyor. Amacı:

  * football-data.co.uk'a çıkışın engellendiği ortamlarda (kapalı ağ, kurumsal
    proxy, bu deponun geliştirildiği sandbox) modeli gerçek veriyle
    çalıştırabilmek,
  * birincil kaynak geçici olarak düştüğünde yedek olmak.
"""

from __future__ import annotations

import logging

from ..config import LEAGUES
from .base import Source, first_present, parse_date, season_codes, to_int
from .footballdata_uk import _result_from_goals, _rows

log = logging.getLogger(__name__)

BASE = (
    "https://raw.githubusercontent.com/datasets/football-datasets/master/"
    "datasets/{slug}/season-{season}.csv"
)


class GithubMirror(Source):
    name = "mirror"
    provides_odds = False

    def fetch(self, leagues: list[str], seasons: list[str] | None = None) -> list[dict]:
        seasons = seasons or season_codes(self.settings.seasons_back)
        newest = seasons[0] if seasons else None
        out: list[dict] = []
        skipped = []

        for code in leagues:
            league = LEAGUES.get(code.upper())
            if league is None or not league.mirror_slug:
                skipped.append(code)
                continue
            for season in seasons:
                max_age = 6.0 if season == newest else 24.0 * 30
                raw = self._download(
                    BASE.format(slug=league.mirror_slug, season=season),
                    f"mirror_{league.code}_{season}.csv",
                    max_age,
                )
                if raw:
                    out.extend(self._parse(_rows(raw), league.code, season))

        if skipped:
            log.info(
                "Ayna kaynağında bulunmayan ligler atlandı: %s "
                "(yalnızca E0, SP1, I1, D1, F1 mevcut)",
                ", ".join(skipped),
            )
        return out

    def _parse(self, rows: list[dict], code: str, season: str) -> list[dict]:
        out = []
        for row in rows:
            date_iso = parse_date(first_present(row, "Date"))
            home = first_present(row, "HomeTeam", "Home")
            away = first_present(row, "AwayTeam", "Away")
            hg = to_int(first_present(row, "FTHG"))
            ag = to_int(first_present(row, "FTAG"))
            if not (date_iso and home and away) or hg is None or ag is None:
                continue
            out.append(
                {
                    "league": code,
                    "season": season,
                    "date": date_iso,
                    "home": str(home).strip(),
                    "away": str(away).strip(),
                    "fthg": hg,
                    "ftag": ag,
                    "ftr": first_present(row, "FTR") or _result_from_goals(hg, ag),
                    "hthg": to_int(first_present(row, "HTHG")),
                    "htag": to_int(first_present(row, "HTAG")),
                    "hs": to_int(first_present(row, "HS")),
                    "as": to_int(first_present(row, "AS")),
                    "hst": to_int(first_present(row, "HST")),
                    "ast": to_int(first_present(row, "AST")),
                    "hc": to_int(first_present(row, "HC")),
                    "ac": to_int(first_present(row, "AC")),
                    "hy": to_int(first_present(row, "HY")),
                    "ay": to_int(first_present(row, "AY")),
                    "hr": to_int(first_present(row, "HR")),
                    "ar": to_int(first_present(row, "AR")),
                    "source": self.name,
                }
            )
        return out
