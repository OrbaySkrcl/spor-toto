"""Veri toplama: kaynaktan indir, normalize et, veritabanına yaz."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Settings
from .sources import get_source, season_codes
from .storage import Database

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    source: str
    fetched: int
    written: int
    leagues: list[str]
    seasons: list[str]
    fixtures: int = 0
    #: Kaynağın sunmadığı lig kodları (ör. TFF 1. Lig). Kullanıcıya gösterilir.
    missing: dict = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"{self.source}: {self.fetched} maç çekildi, {self.written} kayıt yazıldı "
            f"({len(self.leagues)} lig, {len(self.seasons)} sezon)"
            + (f", {self.fixtures} yaklaşan maç" if self.fixtures else "")
            + (
                f" | kaynakta yok: {', '.join(sorted(self.missing))}"
                if self.missing
                else ""
            )
        )


def ingest(
    settings: Settings,
    leagues: list[str] | None = None,
    seasons: list[str] | None = None,
    source_name: str | None = None,
    with_fixtures: bool = True,
) -> IngestResult:
    """Kaynaktan maçları çekip veritabanına yazar. Tekrar çalıştırmak güvenlidir."""
    settings.ensure_dirs()
    leagues = leagues or settings.leagues
    seasons = seasons or season_codes(settings.seasons_back)
    source = get_source(source_name or settings.source, settings)
    db = Database(settings.db_path)

    rows = source.fetch(leagues, seasons)
    written = db.upsert_matches(rows)

    fixtures = 0
    if with_fixtures:
        try:
            upcoming = source.fetch_fixtures()
        except Exception as exc:
            log.warning("Fikstür çekilemedi: %s", exc)
            upcoming = []
        # Kaynak tüm liglerin fikstürünü verir; yalnızca eğitilen ligleri
        # saklarız. Aksi hâlde `hafta` komutu modelin hiç görmediği liglerden
        # maç gösterir ve o maçlarda yalnızca zayıf bileşenler çalışır.
        wanted = {code.upper() for code in leagues}
        upcoming = [f for f in upcoming if (f.get("league") or "").upper() in wanted]
        if upcoming:
            # Fikstürleri sonuçsuz maç olarak yazarız; oynanınca sonuç dolar.
            db.upsert_fixtures(upcoming)
            fixtures = len(upcoming)

    db.set_meta("last_ingest", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    db.set_meta("last_ingest_source", source.name)
    db.set_meta("missing_leagues", json.dumps(sorted(source.missing), ensure_ascii=False))
    if source.missing:
        log.warning(
            "Kaynak şu ligleri sunmuyor: %s", ", ".join(sorted(source.missing))
        )

    result = IngestResult(
        source.name, len(rows), written, leagues, seasons, fixtures, dict(source.missing)
    )
    log.info(result.summary())
    return result
