"""Veri kaynağı ayrıştırıcıları.

football-data.co.uk birincil kaynaktır ama bu depo geliştirilirken kullanılan
ortamın egress politikası o alan adını engelliyordu. Ayrıştırıcı bu yüzden
gerçek dosya biçiminin birebir örnekleriyle test edilir — canlı indirme
yapılmadan doğruluğu güvence altına alınır.
"""

from datetime import date

import pytest

from sportoto.sources.base import parse_date, season_codes, to_float, to_int
from sportoto.sources.footballdata_uk import FootballDataUK, _normalize_extra_season, _rows
from sportoto.sources.local import LocalCSV
from sportoto.sources.synthetic import generate_league

# football-data.co.uk "main" düzeni (T1.csv biçimi, sütun adları gerçek)
MAIN_CSV = b"""Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR,B365H,B365D,B365A,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA,B365CH,B365CD,B365CA,AvgCH,AvgCD,AvgCA
T1,09/08/2024,20:00,Galatasaray,Hatayspor,4,0,H,2,0,H,18,6,9,2,11,14,7,2,1,3,0,0,1.25,6.00,11.00,1.30,6.50,13.00,1.26,6.10,11.50,1.22,6.50,13.00,1.23,6.40,12.20
T1,10/08/2024,17:00,Kasimpasa,Trabzonspor,1,2,A,0,1,A,9,15,3,6,16,12,4,7,2,2,0,0,3.40,3.50,2.10,3.60,3.70,2.20,3.45,3.55,2.12,3.75,3.60,2.00,3.70,3.55,2.02
T1,11/08/2024,,Fenerbahce,Adana Demirspor,2,2,D,1,1,D,20,7,8,3,10,15,9,3,1,4,0,1,1.30,5.50,10.00,1.35,6.00,11.00,1.31,5.60,10.20,,,,,,
"""

# "extra" düzeni (new/ARG.csv biçimi: tüm sezonlar tek dosyada)
EXTRA_CSV = b"""Country,League,Season,Date,Time,Home,Away,HG,AG,Res,PH,PD,PA,MaxH,MaxD,MaxA,AvgH,AvgD,AvgA
Argentina,Liga Profesional,2024,12/04/2024,22:00,Boca Juniors,River Plate,1,0,H,2.50,3.10,3.00,2.60,3.20,3.20,2.45,3.05,2.95
Argentina,Liga Profesional,2024,13/04/2024,20:30,Racing Club,Independiente,2,2,D,2.10,3.20,3.80,2.20,3.30,4.00,2.08,3.15,3.70
Argentina,Liga Profesional,2023,15/04/2023,21:00,Boca Juniors,Racing Club,0,1,A,2.30,3.10,3.40,2.40,3.20,3.50,2.28,3.05,3.35
"""


@pytest.fixture
def parser(settings):
    return FootballDataUK(settings)


def test_main_layout_parses_results_and_odds(parser):
    rows = parser._parse_main(_rows(MAIN_CSV), "T1", "2425")
    assert len(rows) == 3

    first = rows[0]
    assert first["date"] == "2024-08-09"          # dd/mm/yyyy -> ISO
    assert (first["home"], first["away"]) == ("Galatasaray", "Hatayspor")
    assert (first["fthg"], first["ftag"], first["ftr"]) == (4, 0, "H")
    assert (first["hthg"], first["htag"]) == (2, 0)
    assert (first["hst"], first["ast"]) == (9, 2)
    # Açılış için piyasa ortalaması (AvgH) tercih edilmeli, tek kitapçı değil.
    assert first["odds_h"] == 1.26
    assert first["codds_h"] == 1.23                # kapanış (AvgCH)


def test_main_layout_handles_missing_closing_odds(parser):
    rows = parser._parse_main(_rows(MAIN_CSV), "T1", "2425")
    last = rows[2]
    assert last["odds_h"] == 1.31                  # açılış var
    assert last["codds_h"] is None                 # kapanış boş
    assert last["ar"] == 1                         # kırmızı kart okundu


def test_extra_layout_filters_by_season(parser):
    rows = parser._parse_extra(_rows(EXTRA_CSV), "ARG", {"2425"})
    assert len(rows) == 2                          # 2023 sezonu elendi
    assert {r["season"] for r in rows} == {"2425"}
    assert rows[0]["home"] == "Boca Juniors"
    assert rows[0]["odds_h"] == 2.45               # AvgH


def test_extra_layout_without_season_filter_keeps_all(parser):
    assert len(parser._parse_extra(_rows(EXTRA_CSV), "ARG", set())) == 3


def test_rows_with_missing_scores_are_skipped(parser):
    csv = b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\nT1,09/08/2024,A,B,,,\n"
    assert parser._parse_main(_rows(csv), "T1", "2425") == []


def test_result_is_derived_when_ftr_column_missing(parser):
    csv = b"Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nT1,09/08/2024,A,B,3,1\n"
    assert parser._parse_main(_rows(csv), "T1", "2425")[0]["ftr"] == "H"


def test_local_source_reads_directory(settings, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "T1_2425.csv").write_bytes(MAIN_CSV)
    rows = LocalCSV(settings).fetch(["T1"], ["2425"])
    assert len(rows) == 3
    assert all(r["league"] == "T1" for r in rows)


def test_local_source_reads_season_subdirectory(settings, tmp_path):
    season_dir = tmp_path / "raw" / "2425"
    season_dir.mkdir(parents=True)
    (season_dir / "T1.csv").write_bytes(MAIN_CSV)
    rows = LocalCSV(settings).fetch(["T1"], ["2425"])
    assert len(rows) == 3
    assert rows[0]["season"] == "2425"


def test_local_source_ignores_unknown_league_files(settings, tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "ZZ9_2425.csv").write_bytes(MAIN_CSV)
    assert LocalCSV(settings).fetch(["T1"], ["2425"]) == []


# --- yardımcılar ---
@pytest.mark.parametrize(
    "value,expected",
    [
        ("09/08/2024", "2024-08-09"),
        ("09/08/24", "2024-08-09"),
        ("2024-08-09", "2024-08-09"),
        ("09.08.2024", "2024-08-09"),
        ("", None),
        ("nan", None),
        (None, None),
        ("abc", None),
    ],
)
def test_parse_date(value, expected):
    assert parse_date(value) == expected


def test_season_codes_uses_july_cutoff():
    assert season_codes(3, date(2026, 9, 2)) == ["2627", "2526", "2425"]
    assert season_codes(3, date(2026, 3, 2)) == ["2526", "2425", "2324"]
    assert season_codes(1, date(2026, 7, 1)) == ["2627"]


def test_extra_season_normalisation():
    assert _normalize_extra_season("2024/2025") == "2425"
    assert _normalize_extra_season("2024") == "2425"
    assert _normalize_extra_season("") is None
    assert _normalize_extra_season("abc") is None


def test_numeric_helpers_reject_invalid_odds():
    assert to_float("1.95") == 1.95
    assert to_float("0") is None          # oran 0 olamaz
    assert to_float("") is None
    assert to_float("nan") is None
    assert to_int("3") == 3
    assert to_int("3.0") == 3
    assert to_int("") is None


def test_synthetic_generator_is_deterministic():
    a = generate_league(n_teams=10, n_seasons=2, seed=1)
    b = generate_league(n_teams=10, n_seasons=2, seed=1)
    assert a == b
    assert generate_league(n_teams=10, n_seasons=2, seed=2) != a


def test_synthetic_results_look_like_football():
    """Ev sahibi avantajı ve beraberlik oranı gerçekçi aralıkta olmalı."""
    rows = generate_league(n_teams=18, n_seasons=6, seed=9)
    shares = {r: sum(1 for x in rows if x["ftr"] == r) / len(rows) for r in "HDA"}
    assert 0.38 < shares["H"] < 0.52
    assert 0.20 < shares["D"] < 0.30
    assert shares["H"] > shares["A"]
