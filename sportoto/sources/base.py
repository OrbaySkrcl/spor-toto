"""Veri kaynağı arayüzü ve ortak yardımcılar."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from pathlib import Path

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """Veri kaynağına ulaşılamadı veya içerik ayrıştırılamadı."""


def season_codes(n_back: int, today: date | None = None) -> list[str]:
    """En yeniden eskiye doğru '2425' biçiminde sezon kodları üretir.

    Avrupa sezonu temmuzda başladığı için kesim ayı 7 alındı.
    """
    today = today or date.today()
    start_year = today.year if today.month >= 7 else today.year - 1
    codes = []
    for i in range(max(1, n_back)):
        y0 = start_year - i
        codes.append(f"{y0 % 100:02d}{(y0 + 1) % 100:02d}")
    return codes


def parse_date(value) -> str | None:
    """Çeşitli tarih biçimlerini ISO 'YYYY-MM-DD' hâline getirir."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", ""}:
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        # İki haneli yıl: 70 ve üstü 19xx sayılır (strptime zaten böyle yapar).
        return parsed.isoformat()
    return None


def to_float(value) -> float | None:
    try:
        f = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if f != f or f <= 0:  # NaN veya geçersiz oran
        return None
    return f


def to_int(value) -> int | None:
    try:
        text = str(value).strip()
        if not text or text.lower() in {"nan", "nat"}:
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def first_present(row, *candidates):
    """Verilen sütun adlarından ilk dolu olanın değerini döner."""
    for name in candidates:
        if name in row:
            value = row[name]
            if value is not None and str(value).strip() not in {"", "nan", "NaN"}:
                return value
    return None


class Source:
    """Tüm veri kaynaklarının uyduğu arayüz."""

    name = "base"
    #: Bu kaynak bahis oranı sağlıyor mu?
    provides_odds = False

    def __init__(self, settings):
        self.settings = settings
        #: İndirilemeyen kaynak dosyaları (lig kodu -> sebep). Kapsam
        #: raporunda kullanılır: kullanıcı hangi ligin verisinin gelmediğini
        #: tahmin etmek zorunda kalmamalı.
        self.missing: dict[str, str] = {}

    def fetch(self, leagues: list[str], seasons: list[str] | None = None) -> list[dict]:
        """Oynanmış maçları `storage.MATCH_COLUMNS` anahtarlarıyla döner."""
        raise NotImplementedError

    def fetch_fixtures(self) -> list[dict]:
        """Yaklaşan maçları döner. Desteklemeyen kaynak boş liste verir."""
        return []

    # -- HTTP + önbellek --------------------------------------------------
    def _cache_path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace(":", "_")
        return Path(self.settings.cache_dir) / safe

    def _download(self, url: str, cache_key: str, max_age_hours: float = 6.0,
                  label: str | None = None) -> bytes | None:
        """URL'yi indirir; taze önbellek varsa ağa çıkmaz. Hata hâlinde None."""
        import requests

        path = self._cache_path(cache_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            age_h = (time.time() - path.stat().st_mtime) / 3600.0
            if age_h < max_age_hours:
                return path.read_bytes()

        try:
            resp = requests.get(url, timeout=self.settings.http_timeout)
            resp.raise_for_status()
        except Exception as exc:  # ağ hatası kalıcı olmasın: bayat önbelleğe düş
            if path.exists():
                log.warning("%s indirilemedi (%s); bayat önbellek kullanılıyor", url, exc)
                return path.read_bytes()
            log.warning("%s indirilemedi: %s", url, exc)
            if label:
                self.missing.setdefault(label, str(exc)[:120])
            return None

        if not resp.content or len(resp.content) < 64:
            if label and not path.exists():
                self.missing.setdefault(label, "kaynakta boş/eksik dosya")
            return path.read_bytes() if path.exists() else None
        path.write_bytes(resp.content)
        return resp.content
