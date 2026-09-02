"""Takım adı çözümleme ve kupon ayrıştırma."""

import pytest

from sportoto.teams import TeamResolver, normalize, parse_coupon, parse_fixture_line

KNOWN = [
    "Fenerbahce", "Galatasaray", "Besiktas", "Trabzonspor", "Buyuksehyr",
    "Karagumruk", "Kasimpasa", "Goztep", "Rizespor", "Konyaspor", "Ankaragucu",
    "Man United", "Man City", "Nott'm Forest", "Sheffield United", "Wolves",
    "Ath Madrid", "Ath Bilbao", "Real Madrid", "Barcelona", "Sociedad",
    "Bayern Munich", "M'gladbach", "Dortmund", "Paris SG", "Sp Lisbon",
]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Fenerbahçe", "Fenerbahce"),
        ("Beşiktaş", "Besiktas"),
        ("Fatih Karagümrük", "Karagumruk"),
        ("RAMS Başakşehir", "Buyuksehyr"),
        ("Çaykur Rizespor", "Rizespor"),
        ("TÜMOSAN Konyaspor", "Konyaspor"),
        ("MKE Ankaragücü", "Ankaragucu"),
        ("Göztepe", "Goztep"),
        ("Kasımpaşa", "Kasimpasa"),
        ("Manchester United", "Man United"),
        ("Man Utd", "Man United"),
        ("Nottingham Forest", "Nott'm Forest"),
        ("Sheffield Utd", "Sheffield United"),
        ("Wolverhampton Wanderers", "Wolves"),
        ("Atlético Madrid", "Ath Madrid"),
        ("Athletic Bilbao", "Ath Bilbao"),
        ("Real Sociedad", "Sociedad"),
        ("Bayern Münih", "Bayern Munich"),
        ("Borussia Mönchengladbach", "M'gladbach"),
        ("Borussia Dortmund", "Dortmund"),
        ("Paris Saint-Germain", "Paris SG"),
        ("Sporting CP", "Sp Lisbon"),
        ("Galatasaray SK", "Galatasaray"),
    ],
)
def test_resolver_matches_turkish_and_european_names(query, expected):
    match = TeamResolver(KNOWN).resolve(query)
    assert match.team == expected, f"{query} -> {match.team} (skor {match.score:.2f})"
    assert match.confident


def test_normalize_strips_diacritics_and_sponsors():
    assert normalize("Çaykur Rizespor") == normalize("Rizespor")
    assert normalize("İstanbulspor") == "istanbulspor"
    assert normalize("") == ""


def test_unknown_team_is_not_confident():
    match = TeamResolver(KNOWN).resolve("Zzz Yyy Kulübü")
    assert not match.confident


def test_ambiguous_match_flagged_as_unconfident():
    """İki aday neredeyse eşit puan alıyorsa eşleşme güvenli sayılmamalı."""
    resolver = TeamResolver(["Sporting Gijon", "Sporting Braga"])
    match = resolver.resolve("Sporting")
    assert not match.confident


@pytest.mark.parametrize(
    "line,expected",
    [
        ("1. Galatasaray - Fenerbahçe", ("Galatasaray", "Fenerbahçe")),
        ("2) Beşiktaş – Trabzonspor", ("Beşiktaş", "Trabzonspor")),
        ("Man United vs Man City", ("Man United", "Man City")),
        ("Real Madrid ile Barcelona", ("Real Madrid", "Barcelona")),
        ("12 - Bayern Munich — Dortmund", ("Bayern Munich", "Dortmund")),
        ("3. Milan - Roma 20:45", ("Milan", "Roma")),
    ],
)
def test_parse_fixture_line(line, expected):
    assert parse_fixture_line(line) == expected


def test_parse_fixture_line_rejects_garbage():
    assert parse_fixture_line("") is None
    assert parse_fixture_line("sadece tek isim") is None


def test_parse_coupon_skips_noise_lines():
    text = """SPOR TOTO 42. HAFTA
1. Galatasaray - Fenerbahçe
2. Beşiktaş - Trabzonspor

son tarih: pazar
3. Milan - Roma
"""
    assert parse_coupon(text) == [
        ("Galatasaray", "Fenerbahçe"),
        ("Beşiktaş", "Trabzonspor"),
        ("Milan", "Roma"),
    ]
