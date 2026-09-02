"""SQLite deposu."""

import pytest

from sportoto.storage import Database, make_match_id


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / "s.db")


def base_row(**overrides):
    row = {
        "league": "T1", "season": "2425", "date": "2024-08-10",
        "home": "Galatasaray", "away": "Fenerbahce",
        "fthg": 2, "ftag": 1, "ftr": "H", "source": "test",
    }
    row.update(overrides)
    return row


def test_match_id_is_stable_and_distinct():
    assert make_match_id("T1", "2024-08-10", "A", "B") == make_match_id(
        "T1", "2024-08-10", "A", "B"
    )
    assert make_match_id("T1", "2024-08-10", "A", "B") != make_match_id(
        "T1", "2024-08-10", "B", "A"
    )


def test_upsert_is_idempotent(db):
    rows = [base_row(), base_row(date="2024-08-11", home="Besiktas", away="Trabzonspor")]
    assert db.upsert_matches(rows) == 2
    db.upsert_matches(rows)
    db.upsert_matches(rows)
    assert len(db.load_matches()) == 2


def test_missing_fields_do_not_erase_existing_data(db):
    """Oransız bir kaynak (ör. GitHub aynası) mevcut oranları silmemeli."""
    db.upsert_matches([base_row(odds_h=1.9, odds_d=3.5, odds_a=4.0)])
    db.upsert_matches([base_row(source="oransız-kaynak")])
    row = db.load_matches().iloc[0]
    assert row["odds_h"] == 1.9
    assert row["source"] == "oransız-kaynak"


def test_rows_without_key_fields_are_skipped(db):
    assert db.upsert_matches([{"league": "T1", "date": None, "home": "A", "away": "B"}]) == 0
    assert db.upsert_matches([]) == 0


def test_played_only_filter(db):
    db.upsert_matches([base_row(), base_row(date="2024-08-20", ftr=None, fthg=None, ftag=None)])
    assert len(db.load_matches(played_only=True)) == 1
    assert len(db.load_matches(played_only=False)) == 2


def test_date_filters(db):
    db.upsert_matches([
        base_row(date="2024-08-01"),
        base_row(date="2024-09-01", home="Besiktas"),
        base_row(date="2024-10-01", home="Trabzonspor"),
    ])
    assert len(db.load_matches(before="2024-09-01")) == 1
    assert len(db.load_matches(since="2024-09-01")) == 2


def test_known_teams_and_league_lookup(db):
    db.upsert_matches([
        base_row(),
        base_row(league="E0", date="2024-08-12", home="Arsenal", away="Chelsea"),
    ])
    assert db.known_teams() == ["Arsenal", "Chelsea", "Fenerbahce", "Galatasaray"]
    assert db.known_teams(leagues=["E0"]) == ["Arsenal", "Chelsea"]
    assert db.team_leagues()["Arsenal"] == "E0"


def test_meta_round_trip(db):
    assert db.get_meta("yok", "varsayılan") == "varsayılan"
    db.set_meta("anahtar", "değer")
    assert db.get_meta("anahtar") == "değer"
    db.set_meta("anahtar", "yeni")
    assert db.get_meta("anahtar") == "yeni"


def test_manual_adjustments_respect_validity_window(db):
    db.add_adjustment("Galatasaray", "2024-08-01", attack=-0.25, valid_to="2024-08-31",
                      note="golcü sakat")
    assert db.active_adjustments("2024-08-15")["Galatasaray"] == (-0.25, 0.0)
    assert "Galatasaray" not in db.active_adjustments("2024-09-15")
    assert "Galatasaray" not in db.active_adjustments("2024-07-15")


def test_stats_reports_odds_coverage(db):
    db.upsert_matches([
        base_row(odds_h=1.9, odds_d=3.5, odds_a=4.0),
        base_row(date="2024-08-11", home="Besiktas"),
    ])
    stats = db.stats()
    assert stats["matches"] == 2
    assert stats["with_odds"] == 1
    assert stats["leagues"] == 1
