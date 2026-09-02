"""Takım adı normalizasyonu ve bulanık eşleştirme.

Spor Toto kuponundaki Türkçe takım adları ile veri kaynağındaki adlar
birebir örtüşmez ("Fatih Karagümrük" ↔ "Karagumruk", "Manchester United" ↔
"Man United"). Bu modül kupon metnini veritabanındaki takımlara bağlar.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass

# Türkçe karakterler NFD ile ayrışmadığı için elle eşlenir.
_TR_MAP = str.maketrans(
    {
        "ı": "i", "İ": "i", "I": "i", "ğ": "g", "Ğ": "g",
        "ü": "u", "Ü": "u", "ş": "s", "Ş": "s",
        "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
        "â": "a", "î": "i", "û": "u", "é": "e", "è": "e", "ê": "e",
        "ø": "o", "å": "a", "æ": "ae", "ß": "ss", "ñ": "n",
    }
)

# Kulüp adlarında bilgi taşımayan jenerik ekler ve sponsor adları.
_STOPWORDS = {
    "fc", "sc", "ac", "cf", "sk", "afc", "cd", "ud", "rc", "ss", "as", "us",
    "sv", "vfl", "vfb", "tsg", "fsv", "bsc", "if", "bk", "ik", "aik", "sd",
    "kulubu", "kulup", "spor kulubu", "club", "calcio", "cp", "sad", "ad",
    "mke", "caykur", "vavacars", "corendon", "yukatel", "atakas", "arabam",
    "bitexen", "gencler", "yilport", "ikas", "net", "gzt", "tumosan", "esenler",
    "sanica", "boru", "hes", "kablo", "royal", "hastanesi", "holding",
    "de", "do", "la", "le", "el", "of", "the", "und", "e",
}

# Elle tanımlı eşanlamlılar: normalize edilmiş anahtar -> kanonik normalize ad.
_ALIASES: dict[str, str] = {}


def _register(canonical: str, *aliases: str) -> None:
    key = _basic_normalize(canonical)
    _ALIASES[key] = key
    for alias in aliases:
        _ALIASES[_basic_normalize(alias)] = key


def _basic_normalize(name: str) -> str:
    """Diakritikleri düşürür, küçük harfe çevirir, noktalamayı temizler."""
    if not name:
        return ""
    text = name.translate(_TR_MAP)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize(name: str) -> str:
    """Eşleştirme anahtarı üretir: jenerik ekleri de atar, eşanlamlıyı uygular."""
    base = _basic_normalize(name)
    if not base:
        return ""
    if base in _ALIASES:
        return _ALIASES[base]
    tokens = [t for t in base.split() if t not in _STOPWORDS]
    stripped = " ".join(tokens) if tokens else base
    return _ALIASES.get(stripped, stripped)


def tokens(name: str) -> set[str]:
    return {t for t in normalize(name).split() if len(t) > 1}


# --------------------------------------------------------------------------
# Eşanlamlı tablosu — kanonik ad veri kaynağındaki (football-data.co.uk) yazım
# --------------------------------------------------------------------------
# Türkiye
_register("Fenerbahce", "Fenerbahçe", "Fener")
_register("Galatasaray", "Galatasaray SK", "Cimbom")
_register("Besiktas", "Beşiktaş", "Beşiktaş JK")
_register("Trabzonspor", "Trabzon")
_register("Buyuksehyr", "Başakşehir", "Istanbul Basaksehir", "Basaksehir", "RAMS Başakşehir")
_register("Ad. Demirspor", "Adana Demirspor", "Adana Demir")
_register("Karagumruk", "Fatih Karagümrük", "Fatih Karagumruk")
_register("Kasimpasa", "Kasımpaşa")
_register("Goztep", "Göztepe", "Goztepe")
_register("Rizespor", "Çaykur Rizespor", "Caykur Rizespor")
_register("Ankaragucu", "MKE Ankaragücü", "Ankaragücü")
_register("Gaziantep", "Gaziantep FK", "Gaziantepspor")
_register("Sivasspor", "Sivas", "EMS Yapı Sivasspor")
_register("Konyaspor", "Konya", "TÜMOSAN Konyaspor")
_register("Alanyaspor", "Corendon Alanyaspor")
_register("Antalyaspor", "Bitexen Antalyaspor")
_register("Kayserispor", "Mondihome Kayserispor")
_register("Samsunspor", "Yılport Samsunspor")
_register("Eyupspor", "Eyüpspor")
_register("Bodrumspor", "Bodrum FK", "Bodrumspor")
_register("Hatayspor", "Atakaş Hatayspor")
_register("Genclerbirligi", "Gençlerbirliği")
_register("Istanbulspor", "İstanbulspor")
_register("Pendikspor", "Pendik")
_register("Umraniye", "Ümraniyespor", "Umraniyespor")
_register("Giresunspor", "Giresun")
_register("Altay", "Altay SK")
_register("Erzurum BB", "BB Erzurumspor", "Erzurumspor")
_register("Denizli", "Denizlispor")
_register("Malatyaspor", "Yeni Malatyaspor")
_register("Kocaelispor", "Kocaeli")

# İngiltere
_register("Man United", "Manchester United", "Manchester Utd", "Man Utd")
_register("Man City", "Manchester City")
_register("Nott'm Forest", "Nottingham Forest", "Nottm Forest", "Forest")
_register("Tottenham", "Tottenham Hotspur", "Spurs")
_register("Wolves", "Wolverhampton", "Wolverhampton Wanderers")
_register("Newcastle", "Newcastle United", "Newcastle Utd")
_register("West Ham", "West Ham United")
_register("Sheffield United", "Sheffield Utd", "Sheff Utd")
_register("Sheffield Weds", "Sheffield Wednesday", "Sheff Wed")
_register("Leicester", "Leicester City")
_register("Leeds", "Leeds United", "Leeds Utd")
_register("Norwich", "Norwich City")
_register("Brighton", "Brighton & Hove Albion", "Brighton and Hove Albion")
_register("Bournemouth", "AFC Bournemouth")
_register("Crystal Palace", "C Palace", "Palace")
_register("Stoke", "Stoke City")
_register("Hull", "Hull City")
_register("Cardiff", "Cardiff City")
_register("Swansea", "Swansea City")
_register("Birmingham", "Birmingham City")
_register("Coventry", "Coventry City")
_register("Ipswich", "Ipswich Town")
_register("Luton", "Luton Town")
_register("QPR", "Queens Park Rangers")
_register("West Brom", "West Bromwich Albion", "WBA", "West Bromwich")

# İspanya
_register("Ath Madrid", "Atletico Madrid", "Atlético Madrid", "Atletico de Madrid")
_register("Ath Bilbao", "Athletic Bilbao", "Athletic Club")
_register("Real Madrid", "R. Madrid")
_register("Barcelona", "FC Barcelona", "Barca")
_register("Sociedad", "Real Sociedad")
_register("Betis", "Real Betis")
_register("Espanol", "Espanyol", "RCD Espanyol")
_register("Vallecano", "Rayo Vallecano")
_register("Celta", "Celta Vigo")
_register("Villarreal", "Villareal")
_register("Valladolid", "Real Valladolid")
_register("Sevilla", "Sevilla FC")
_register("Ferencvaros", "Ferencvárosi TC")

# İtalya
_register("Inter", "Internazionale", "Inter Milan")
_register("Milan", "AC Milan")
_register("Roma", "AS Roma")
_register("Napoli", "SSC Napoli")
_register("Juventus", "Juve")
_register("Verona", "Hellas Verona")

# Almanya
_register("Bayern Munich", "Bayern Münih", "Bayern Munchen", "FC Bayern", "Bayern")
_register("Dortmund", "Borussia Dortmund", "BVB")
_register("M'gladbach", "Borussia Monchengladbach", "Borussia Mönchengladbach", "Gladbach")
_register("Ein Frankfurt", "Eintracht Frankfurt", "Frankfurt")
_register("Leverkusen", "Bayer Leverkusen", "Bayer 04 Leverkusen")
_register("RB Leipzig", "Leipzig")
_register("Hoffenheim", "TSG Hoffenheim", "1899 Hoffenheim")
_register("Stuttgart", "VfB Stuttgart")
_register("Werder Bremen", "Bremen")
_register("Wolfsburg", "VfL Wolfsburg")
_register("Mainz", "Mainz 05", "FSV Mainz")
_register("Schalke 04", "Schalke")
_register("FC Koln", "Koln", "Köln", "1. FC Köln", "Cologne")
_register("Hertha", "Hertha Berlin", "Hertha BSC")

# Fransa
_register("Paris SG", "Paris Saint-Germain", "PSG", "Paris Saint Germain")
_register("Marseille", "Olympique Marseille", "OM")
_register("Lyon", "Olympique Lyonnais", "OL")
_register("St Etienne", "Saint-Etienne", "Saint Etienne", "ASSE")
_register("Paris FC", "Paris")

# Hollanda / Portekiz / Yunanistan / Belçika / İskoçya
_register("PSV Eindhoven", "PSV")
_register("Ajax", "AFC Ajax")
_register("Feyenoord", "Feyenoord Rotterdam")
_register("AZ Alkmaar", "AZ")
_register("Sp Lisbon", "Sporting", "Sporting CP", "Sporting Lisbon")
_register("Porto", "FC Porto")
_register("Benfica", "SL Benfica")
_register("Sp Braga", "Braga", "SC Braga")
_register("Guimaraes", "Vitoria Guimaraes")
_register("Olympiakos", "Olympiacos", "Olympiacos Piraeus")
_register("Panathinaikos", "Panathinaikos FC")
_register("AEK", "AEK Athens")
_register("PAOK", "PAOK Salonika")
_register("Club Brugge", "Brugge", "Club Bruges")
_register("Anderlecht", "RSC Anderlecht")
_register("Standard", "Standard Liege")
_register("Celtic", "Celtic FC")
_register("Rangers", "Glasgow Rangers")


#: Bu eşiğin altındaki eşleşmeler kabul edilmez. Bulanık arama her zaman bir
#: "en yakın" aday döndürür; %12 benzerlikle bulunan takımı kabul etmek, model
#: tamamen alakasız bir takımın gücüyle güvenli görünen bir tahmin üretmesi
#: demektir. Tanımamak, yanlış tanımaktan iyidir.
MIN_ACCEPT_SCORE = 0.45


@dataclass
class TeamMatch:
    """Bir bulanık eşleştirmenin sonucu."""

    query: str
    team: str | None
    score: float
    alternatives: list[tuple[str, float]]

    @property
    def usable(self) -> bool:
        """Eşleşme kullanılabilecek kadar yakın mı?"""
        return self.team is not None and self.score >= MIN_ACCEPT_SCORE

    @property
    def confident(self) -> bool:
        """Eşleşme güvenli mi? İkinci adaya net fark varsa evet."""
        if self.team is None or self.score < 0.62:
            return False
        if self.alternatives and self.score - self.alternatives[0][1] < 0.05:
            return False
        return True


def _similarity(a_norm: str, b_norm: str) -> float:
    """0-1 arası benzerlik: karakter dizisi + token örtüşmesi karması."""
    if not a_norm or not b_norm:
        return 0.0
    if a_norm == b_norm:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a_norm, b_norm).ratio()

    ta = {t for t in a_norm.split() if len(t) > 1}
    tb = {t for t in b_norm.split() if len(t) > 1}
    if ta and tb:
        jaccard = len(ta & tb) / len(ta | tb)
        # Tam token kapsaması ("Porto" ⊂ "FC Porto") güçlü sinyal.
        containment = len(ta & tb) / min(len(ta), len(tb))
    else:
        jaccard = containment = 0.0

    # Biri diğerinin ön eki ise ("Man Utd" / "Man United") ek puan.
    prefix = 0.0
    shorter, longer = sorted((a_norm, b_norm), key=len)
    if len(shorter) >= 4 and longer.startswith(shorter):
        prefix = 1.0

    return max(
        0.55 * ratio + 0.30 * jaccard + 0.15 * containment,
        0.60 * containment + 0.40 * ratio,
        0.70 * prefix + 0.30 * ratio,
    )


class TeamResolver:
    """Bilinen takım adları kümesine karşı bulanık arama yapar."""

    def __init__(self, known_teams: list[str]):
        self.known = list(dict.fromkeys(known_teams))
        self._index: dict[str, list[str]] = {}
        for team in self.known:
            self._index.setdefault(normalize(team), []).append(team)

    def resolve(self, query: str, limit: int = 3) -> TeamMatch:
        norm = normalize(query)
        if not norm:
            return TeamMatch(query, None, 0.0, [])

        # 1) Doğrudan / eşanlamlı isabet.
        if norm in self._index:
            return TeamMatch(query, self._index[norm][0], 1.0, [])

        # 2) Bulanık tarama.
        scored = sorted(
            ((team, _similarity(norm, normalize(team))) for team in self.known),
            key=lambda kv: kv[1],
            reverse=True,
        )
        if not scored:
            return TeamMatch(query, None, 0.0, [])
        best_team, best_score = scored[0]
        return TeamMatch(query, best_team, best_score, scored[1 : 1 + limit])

    def resolve_all(self, queries: list[str]) -> list[TeamMatch]:
        return [self.resolve(q) for q in queries]


_SEPARATORS = re.compile(r"\s+(?:-|–|—|vs\.?|v\.?|ile)\s+", re.IGNORECASE)


def parse_fixture_line(line: str) -> tuple[str, str] | None:
    """"1. Galatasaray - Fenerbahçe" gibi bir satırdan (ev, deplasman) çıkarır."""
    text = line.strip()
    if not text:
        return None
    # Baştaki sıra numarasını at: "1)", "1.", "01 -", "1 "
    text = re.sub(r"^\s*\d{1,2}\s*[\.\)\-:]\s*", "", text)
    parts = _SEPARATORS.split(text, maxsplit=1)
    if len(parts) != 2:
        return None
    home, away = parts[0].strip(" \t-–—"), parts[1].strip(" \t-–—")
    # Sondaki tarih/saat/oran artıklarını at.
    away = re.sub(r"\s+\d{1,2}[:.]\d{2}.*$", "", away).strip()
    if not home or not away:
        return None
    return home, away


def parse_coupon(text: str) -> list[tuple[str, str]]:
    """Çok satırlı kupon metnini maç listesine çevirir."""
    fixtures = []
    for line in text.splitlines():
        parsed = parse_fixture_line(line)
        if parsed:
            fixtures.append(parsed)
    return fixtures
