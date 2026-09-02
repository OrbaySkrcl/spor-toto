"""football-data.co.uk — birincil kaynak.

Neden bu kaynak:
  * API anahtarı istemez, ücretsiz ve kullanım kotası yok.
  * Maç sonuçlarının yanında **bahis oranlarını** da verir (açılış + kapanış),
    ki bu modelin en güçlü tek girdisi.
  * Türkiye Süper Lig (T1) dahil 30'dan fazla ligi ve 1993'e uzanan arşivi kapsar.
  * `fixtures.csv` ile oynanmamış maçların güncel oranlarını verir.

Not: Bu depo geliştirilirken kullanılan sandbox ortamının egress politikası bu
alan adını engelliyordu. Kod Railway/yerel makinede sorunsuz çalışır; engelli
ortamlarda `--source mirror` veya `--source local` kullanın.
"""

from __future__ import annotations

import csv
import io
import logging

from ..config import LEAGUES
from .base import Source, first_present, parse_date, season_codes, to_float, to_int

log = logging.getLogger(__name__)

BASE_MAIN = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"
BASE_EXTRA = "https://www.football-data.co.uk/new/{code}.csv"
FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"

# Oran sütunu adayları — önce piyasa ortalaması/maksimumu, sonra tek kitapçılar.
# Ortalama tek bir kitapçıdan daha az gürültülüdür; Pinnacle (PS/P) en keskinidir.
OPEN_H = ("AvgH", "BbAvH", "PSH", "PH", "B365H", "MaxH", "BbMxH", "WHH", "IWH")
OPEN_D = ("AvgD", "BbAvD", "PSD", "PD", "B365D", "MaxD", "BbMxD", "WHD", "IWD")
OPEN_A = ("AvgA", "BbAvA", "PSA", "PA", "B365A", "MaxA", "BbMxA", "WHA", "IWA")
CLOSE_H = ("AvgCH", "PSCH", "PCH", "B365CH", "MaxCH")
CLOSE_D = ("AvgCD", "PSCD", "PCD", "B365CD", "MaxCD")
CLOSE_A = ("AvgCA", "PSCA", "PCA", "B365CA", "MaxCA")


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _rows(raw: bytes) -> list[dict]:
    text = _decode(raw)
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for row in reader:
        # Kaynak dosyaların sonunda boş satırlar ve fazladan virgüller olabilir.
        clean = {
            (k or "").strip(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
            if k and k.strip()
        }
        if clean:
            out.append(clean)
    return out


def _result_from_goals(hg: int | None, ag: int | None) -> str | None:
    if hg is None or ag is None:
        return None
    return "H" if hg > ag else ("A" if hg < ag else "D")


class FootballDataUK(Source):
    name = "footballdata"
    provides_odds = True

    def fetch(self, leagues: list[str], seasons: list[str] | None = None) -> list[dict]:
        seasons = seasons or season_codes(self.settings.seasons_back)
        newest = seasons[0] if seasons else None
        out: list[dict] = []

        for code in leagues:
            league = LEAGUES.get(code.upper())
            if league is None:
                log.warning("Tanımsız lig kodu atlandı: %s", code)
                continue

            if league.layout == "extra":
                # Tek dosyada tüm sezonlar var; sezonu satır içinden süzeriz.
                raw = self._download(
                    BASE_EXTRA.format(code=league.code), f"fd_extra_{league.code}.csv", 12.0
                )
                if raw:
                    out.extend(self._parse_extra(_rows(raw), league.code, set(seasons)))
                continue

            for season in seasons:
                # Güncel sezon sık değişir, geçmiş sezonlar dondu sayılır.
                max_age = 6.0 if season == newest else 24.0 * 30
                raw = self._download(
                    BASE_MAIN.format(season=season, code=league.code),
                    f"fd_{league.code}_{season}.csv",
                    max_age,
                )
                if raw:
                    out.extend(self._parse_main(_rows(raw), league.code, season))
        return out

    def fetch_fixtures(self) -> list[dict]:
        """Oynanmamış maçlar + güncel oranlar (haftalık kupon için)."""
        raw = self._download(FIXTURES_URL, "fd_fixtures.csv", 1.0)
        if not raw:
            return []
        fixtures = []
        for row in _rows(raw):
            code = (first_present(row, "Div") or "").strip().upper()
            date_iso = parse_date(first_present(row, "Date"))
            home = first_present(row, "HomeTeam", "Home")
            away = first_present(row, "AwayTeam", "Away")
            if not (code and date_iso and home and away):
                continue
            fixtures.append(
                {
                    "league": code,
                    "date": date_iso,
                    "time": first_present(row, "Time"),
                    "home": str(home).strip(),
                    "away": str(away).strip(),
                    "odds_h": to_float(first_present(row, *OPEN_H)),
                    "odds_d": to_float(first_present(row, *OPEN_D)),
                    "odds_a": to_float(first_present(row, *OPEN_A)),
                    "source": self.name,
                }
            )
        return fixtures

    # -- ayrıştırma -------------------------------------------------------
    def _parse_main(self, rows: list[dict], code: str, season: str) -> list[dict]:
        out = []
        for row in rows:
            date_iso = parse_date(first_present(row, "Date"))
            home = first_present(row, "HomeTeam", "Home")
            away = first_present(row, "AwayTeam", "Away")
            hg = to_int(first_present(row, "FTHG", "HG"))
            ag = to_int(first_present(row, "FTAG", "AG"))
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
                    "ftr": (first_present(row, "FTR", "Res") or _result_from_goals(hg, ag)),
                    "hthg": to_int(first_present(row, "HTHG")),
                    "htag": to_int(first_present(row, "HTAG")),
                    "hs": to_int(first_present(row, "HS")),
                    "as": to_int(first_present(row, "AS")),
                    "hst": to_int(first_present(row, "HST")),
                    "ast": to_int(first_present(row, "AST")),
                    "hc": to_int(first_present(row, "HC")),
                    "ac": to_int(first_present(row, "AC")),
                    "hf": to_int(first_present(row, "HF")),
                    "af": to_int(first_present(row, "AF")),
                    "hy": to_int(first_present(row, "HY")),
                    "ay": to_int(first_present(row, "AY")),
                    "hr": to_int(first_present(row, "HR")),
                    "ar": to_int(first_present(row, "AR")),
                    "odds_h": to_float(first_present(row, *OPEN_H)),
                    "odds_d": to_float(first_present(row, *OPEN_D)),
                    "odds_a": to_float(first_present(row, *OPEN_A)),
                    "codds_h": to_float(first_present(row, *CLOSE_H)),
                    "codds_d": to_float(first_present(row, *CLOSE_D)),
                    "codds_a": to_float(first_present(row, *CLOSE_A)),
                    "source": self.name,
                }
            )
        return out

    def _parse_extra(self, rows: list[dict], code: str, wanted: set[str]) -> list[dict]:
        out = []
        for row in rows:
            date_iso = parse_date(first_present(row, "Date"))
            home = first_present(row, "Home", "HomeTeam")
            away = first_present(row, "Away", "AwayTeam")
            hg = to_int(first_present(row, "HG", "FTHG"))
            ag = to_int(first_present(row, "AG", "FTAG"))
            if not (date_iso and home and away) or hg is None or ag is None:
                continue
            # "2024/2025" veya "2024" -> "2425"
            season_raw = str(first_present(row, "Season") or "").strip()
            season = _normalize_extra_season(season_raw)
            if wanted and season and season not in wanted:
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
                    "ftr": (first_present(row, "Res", "FTR") or _result_from_goals(hg, ag)),
                    "odds_h": to_float(first_present(row, *OPEN_H)),
                    "odds_d": to_float(first_present(row, *OPEN_D)),
                    "odds_a": to_float(first_present(row, *OPEN_A)),
                    "codds_h": to_float(first_present(row, *CLOSE_H)),
                    "codds_d": to_float(first_present(row, *CLOSE_D)),
                    "codds_a": to_float(first_present(row, *CLOSE_A)),
                    "source": self.name,
                }
            )
        return out


def _normalize_extra_season(raw: str) -> str | None:
    """'2024/2025' -> '2425', '2024' -> '2425' (tek yıllık takvim ligleri)."""
    if not raw:
        return None
    if "/" in raw:
        a, b = raw.split("/", 1)
        try:
            return f"{int(a) % 100:02d}{int(b) % 100:02d}"
        except ValueError:
            return None
    try:
        y = int(raw)
    except ValueError:
        return None
    return f"{y % 100:02d}{(y + 1) % 100:02d}"
