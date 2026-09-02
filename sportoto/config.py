"""Yapılandırma: lig tanımları, model hiperparametreleri, çalışma ortamı ayarları.

Tüm ayarlar ortam değişkeni ile ezilebilir; böylece Railway üzerinde kod
değiştirmeden davranış ayarlanabilir.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

# --------------------------------------------------------------------------
# Lig tanımları
# --------------------------------------------------------------------------
# football-data.co.uk iki farklı dosya düzeni kullanır:
#   "main"  -> mmz4281/<sezon>/<kod>.csv     (sezon başına bir dosya, zengin sütunlar)
#   "extra" -> new/<kod>.csv                 (tüm sezonlar tek dosyada, sade sütunlar)


@dataclass(frozen=True)
class League:
    code: str           # football-data.co.uk kodu (T1, E0, ARG ...)
    name: str           # okunabilir ad
    country: str
    layout: str         # "main" | "extra"
    tier: int = 1
    # GitHub aynasındaki karşılığı (yalnızca 5 büyük lig için mevcut, oransız)
    mirror_slug: str | None = None
    #: Gol modelinin birlikte kestirileceği lig grubu (ülke piramidi).
    #: Spor Toto kuponları sık sık aynı ülkenin farklı seviyelerini karıştırır
    #: (Süper Lig + 1. Lig). Takım güçleri ancak takımların birbiriyle bağlantılı
    #: olduğu bir kümede karşılaştırılabilir; küme düşme/çıkma sayesinde aynı
    #: ülkenin seviyeleri bağlantılıdır, farklı ülkeler değildir.
    group: str = ""

    @property
    def group_key(self) -> str:
        return self.group or self.code


LEAGUES: dict[str, League] = {
    lg.code: lg
    for lg in [
        # --- Türkiye ---
        League("T1", "Süper Lig", "Türkiye", "main", 1, group="TR"),
        # TFF 1. Lig. Kaynakta her sezon bulunmayabilir; yoksa indirme sessizce
        # atlanır. Spor Toto listeleri Süper Lig ile 1. Lig'i sık karıştırdığı
        # için varsa büyük kazanç, yoksa maliyeti yok.
        League("T2", "1. Lig", "Türkiye", "main", 2, group="TR"),
        # --- İngiltere ---
        League("E0", "Premier League", "İngiltere", "main", 1, "premier-league", group="EN"),
        League("E1", "Championship", "İngiltere", "main", 2, group="EN"),
        League("E2", "League One", "İngiltere", "main", 3, group="EN"),
        League("E3", "League Two", "İngiltere", "main", 4, group="EN"),
        League("EC", "National League", "İngiltere", "main", 5, group="EN"),
        # --- İspanya ---
        League("SP1", "La Liga", "İspanya", "main", 1, "la-liga", group="ES"),
        League("SP2", "La Liga 2", "İspanya", "main", 2, group="ES"),
        # --- İtalya ---
        League("I1", "Serie A", "İtalya", "main", 1, "serie-a", group="IT"),
        League("I2", "Serie B", "İtalya", "main", 2, group="IT"),
        # --- Almanya ---
        League("D1", "Bundesliga", "Almanya", "main", 1, "bundesliga", group="DE"),
        League("D2", "2. Bundesliga", "Almanya", "main", 2, group="DE"),
        # --- Fransa ---
        League("F1", "Ligue 1", "Fransa", "main", 1, "ligue-1", group="FR"),
        League("F2", "Ligue 2", "Fransa", "main", 2, group="FR"),
        # --- Diğer Avrupa (main düzeni) ---
        League("N1", "Eredivisie", "Hollanda", "main", 1),
        League("B1", "Jupiler Pro League", "Belçika", "main", 1),
        League("P1", "Primeira Liga", "Portekiz", "main", 1),
        League("G1", "Super League", "Yunanistan", "main", 1),
        League("SC0", "Premiership", "İskoçya", "main", 1, group="SC"),
        League("SC1", "Championship", "İskoçya", "main", 2, group="SC"),
        # --- extra düzeni (tek dosya, tüm sezonlar) ---
        League("ARG", "Liga Profesional", "Arjantin", "extra", 1),
        League("AUT", "Bundesliga", "Avusturya", "extra", 1),
        League("BRA", "Serie A", "Brezilya", "extra", 1),
        League("DNK", "Superliga", "Danimarka", "extra", 1),
        League("FIN", "Veikkausliiga", "Finlandiya", "extra", 1),
        League("IRL", "Premier Division", "İrlanda", "extra", 1),
        League("JPN", "J1 League", "Japonya", "extra", 1),
        League("MEX", "Liga MX", "Meksika", "extra", 1),
        League("NOR", "Eliteserien", "Norveç", "extra", 1),
        League("POL", "Ekstraklasa", "Polonya", "extra", 1),
        League("ROU", "Liga I", "Romanya", "extra", 1),
        League("SWE", "Allsvenskan", "İsveç", "extra", 1),
        League("SWZ", "Super League", "İsviçre", "extra", 1),
        League("USA", "MLS", "ABD", "extra", 1),
    ]
}

def league_group(code: str | None) -> str | None:
    """Lig kodundan gol modelinin kestirim grubunu döner."""
    if not code:
        return None
    league = LEAGUES.get(str(code).upper())
    return league.group_key if league else str(code).upper()


#: Spor Toto kuponlarında en sık görülen ligler — varsayılan indirme kümesi.
DEFAULT_LEAGUES = [
    "T1", "T2", "E0", "E1", "SP1", "SP2", "I1", "I2", "D1", "D2",
    "F1", "F2", "N1", "B1", "P1", "G1", "SC0",
]


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class ModelConfig:
    """Model hiperparametreleri."""

    #: Dixon-Coles zaman ağırlığı. w = exp(-xi * gün). 0.0018 ≈ yarı ömür 385 gün.
    dc_xi: float = field(default_factory=lambda: _env_float("SPORTOTO_DC_XI", 0.0018))
    #: Skor matrisinde dikkate alınan en yüksek gol sayısı.
    dc_max_goals: int = field(default_factory=lambda: _env_int("SPORTOTO_DC_MAX_GOALS", 12))
    #: Bir ligi eğitmek için gereken en az maç sayısı.
    dc_min_matches: int = field(default_factory=lambda: _env_int("SPORTOTO_DC_MIN_MATCHES", 150))
    #: Bir takımın modele girmesi için gereken en az maç sayısı.
    dc_min_team_matches: int = field(default_factory=lambda: _env_int("SPORTOTO_DC_MIN_TEAM", 5))

    #: Elo K katsayısı ve iç saha avantajı (Elo puanı cinsinden).
    elo_k: float = field(default_factory=lambda: _env_float("SPORTOTO_ELO_K", 20.0))
    elo_home_advantage: float = field(default_factory=lambda: _env_float("SPORTOTO_ELO_HFA", 60.0))
    elo_start: float = 1500.0
    #: Sezonlar arası ortalamaya çekme oranı (0 = yok, 1 = tam sıfırlama).
    elo_season_regression: float = field(
        default_factory=lambda: _env_float("SPORTOTO_ELO_REGRESSION", 0.25)
    )

    #: Oran → olasılık dönüşümünde marj temizleme yöntemi.
    margin_method: str = field(default_factory=lambda: os.environ.get("SPORTOTO_MARGIN", "power"))

    #: Blend ağırlıklarının fit edileceği en son N maç.
    blend_fit_window: int = field(default_factory=lambda: _env_int("SPORTOTO_BLEND_WINDOW", 6000))
    #: Log-pool sonrası olasılık tabanı (aşırı uç değerleri yumuşatır).
    prob_floor: float = 0.005


@dataclass
class CouponConfig:
    """Spor Toto kupon kuralları."""

    n_matches: int = 15
    #: Kolon başına ücret (TL). Spor Toto bunu zaman zaman güncellediği için
    #: ortam değişkeni ile ayarlanabilir tutuldu — güncel değeri siz girin.
    column_price: float = field(default_factory=lambda: _env_float("SPORTOTO_COLUMN_PRICE", 5.0))
    #: Varsayılan kupon bütçesi (TL).
    default_budget: float = field(default_factory=lambda: _env_float("SPORTOTO_BUDGET", 500.0))
    #: Tek bir kuponda izin verilen en fazla kolon.
    max_columns: int = field(default_factory=lambda: _env_int("SPORTOTO_MAX_COLUMNS", 1_000_000))


def _default_data_dir() -> Path:
    """Veri klasörünü seçer.

    Sıralama: `SPORTOTO_DATA_DIR` → bağlı bir kalıcı disk (Railway volume
    tipik olarak `/data`) → yerel `data/`. Kalıcı diskin kendiliğinden
    bulunması, Railway'de elle ayarlanması gereken değişken sayısını
    azaltır: volume bağlandıysa veri oraya yazılır ve yeniden dağıtımlarda
    kaybolmaz.
    """
    explicit = os.environ.get("SPORTOTO_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    for candidate in (Path("/data"), Path("/app/data")):
        if candidate.is_dir() and os.access(candidate, os.W_OK):
            return candidate
    return Path("data")


@dataclass
class Settings:
    data_dir: Path = field(default_factory=_default_data_dir)
    db_path: Path | None = None
    source: str = field(default_factory=lambda: os.environ.get("SPORTOTO_SOURCE", "footballdata"))
    leagues: list[str] = field(
        default_factory=lambda: [
            c.strip().upper()
            for c in os.environ.get("SPORTOTO_LEAGUES", ",".join(DEFAULT_LEAGUES)).split(",")
            if c.strip()
        ]
    )
    #: Kaç sezon geriye inilecek (güncel sezon dahil).
    seasons_back: int = field(default_factory=lambda: _env_int("SPORTOTO_SEASONS", 8))
    http_timeout: int = field(default_factory=lambda: _env_int("SPORTOTO_HTTP_TIMEOUT", 60))
    model: ModelConfig = field(default_factory=ModelConfig)
    coupon: CouponConfig = field(default_factory=CouponConfig)

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if self.db_path is None:
            env_db = os.environ.get("SPORTOTO_DB")
            self.db_path = Path(env_db) if env_db else self.data_dir / "sportoto.db"
        self.db_path = Path(self.db_path)

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def with_overrides(self, **kwargs) -> "Settings":
        return replace(self, **kwargs)


def load_settings(**overrides) -> Settings:
    """Ortam değişkenlerinden ayarları yükler, verilen anahtarlarla ezer."""
    settings = Settings()
    if overrides:
        settings = replace(settings, **{k: v for k, v in overrides.items() if v is not None})
        settings.__post_init__()
    return settings
