"""Yerel CSV klasörü — elle indirilen dosyalar için.

`SPORTOTO_DATA_DIR/raw/` altına football-data.co.uk biçiminde CSV bırakmak
yeterli. Dosya adından lig ve sezon çıkarılır: `T1_2425.csv`, `E0-2324.csv`
veya klasör düzeni `raw/2425/T1.csv`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..config import LEAGUES
from .base import Source
from .footballdata_uk import FootballDataUK, _rows

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^(?P<code>[A-Z]{1,3}\d?)[_\-]?(?P<season>\d{4})?$", re.IGNORECASE)


class LocalCSV(Source):
    name = "local"
    provides_odds = True

    def fetch(self, leagues: list[str], seasons: list[str] | None = None) -> list[dict]:
        root = Path(self.settings.data_dir) / "raw"
        if not root.exists():
            log.warning("Yerel kaynak klasörü yok: %s", root)
            return []

        wanted = {c.upper() for c in leagues} if leagues else None
        wanted_seasons = set(seasons) if seasons else None
        parser = FootballDataUK(self.settings)
        out: list[dict] = []

        for path in sorted(root.rglob("*.csv")):
            code, season = self._identify(path)
            if code is None:
                log.warning("Lig kodu çözülemedi, atlandı: %s", path)
                continue
            if wanted and code not in wanted:
                continue
            if wanted_seasons and season and season not in wanted_seasons:
                continue
            rows = _rows(path.read_bytes())
            league = LEAGUES.get(code)
            if league is not None and league.layout == "extra":
                out.extend(parser._parse_extra(rows, code, wanted_seasons or set()))
            else:
                out.extend(parser._parse_main(rows, code, season or ""))
        return out

    def _identify(self, path: Path) -> tuple[str | None, str | None]:
        """Dosya adı ve üst klasörden (lig kodu, sezon) çıkarır."""
        match = _NAME_RE.match(path.stem)
        if not match:
            return None, None
        code = match.group("code").upper()
        if code not in LEAGUES:
            return None, None
        season = match.group("season")
        if not season and re.fullmatch(r"\d{4}", path.parent.name):
            season = path.parent.name
        return code, season
