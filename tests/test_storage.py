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


def test_load_fixtures_returns_only_unplayed_matches_in_window(db):
    from datetime import date, timedelta

    today = date.today()
    db.upsert_matches([
        # oynanmış — dönmemeli
        base_row(date=(today - timedelta(days=3)).isoformat()),
        # yaklaşan — dönmeli
        base_row(date=(today + timedelta(days=2)).isoformat(), home="Besiktas",
                 fthg=None, ftag=None, ftr=None, odds_h=1.8, odds_d=3.6, odds_a=4.5),
        # pencere dışı — dönmemeli
        base_row(date=(today + timedelta(days=40)).isoformat(), home="Trabzonspor",
                 fthg=None, ftag=None, ftr=None),
    ])
    fixtures = db.load_fixtures(days=8)
    assert len(fixtures) == 1
    assert fixtures.iloc[0]["home"] == "Besiktas"
    assert fixtures.iloc[0]["odds_h"] == 1.8
    assert len(db.load_fixtures(days=60)) == 2


def test_load_fixtures_filters_by_league(db):
    from datetime import date, timedelta

    soon = (date.today() + timedelta(days=1)).isoformat()
    db.upsert_matches([
        base_row(date=soon, fthg=None, ftag=None, ftr=None),
        base_row(league="E0", date=soon, home="Arsenal", away="Chelsea",
                 fthg=None, ftag=None, ftr=None),
    ])
    assert len(db.load_fixtures(days=8, leagues=["E0"])) == 1
    assert len(db.load_fixtures(days=8)) == 2


def test_fixture_row_is_updated_when_match_is_played(db):
    """Fikstür olarak yazılan satır, sonuç gelince aynı satır olarak güncellenmeli."""
    from datetime import date, timedelta

    soon = (date.today() + timedelta(days=1)).isoformat()
    db.upsert_matches([base_row(date=soon, fthg=None, ftag=None, ftr=None,
                                odds_h=1.9, odds_d=3.5, odds_a=4.0)])
    assert len(db.load_fixtures(days=8)) == 1

    db.upsert_matches([base_row(date=soon, fthg=3, ftag=1, ftr="H")])
    assert len(db.load_fixtures(days=8)) == 0
    played = db.load_matches()
    assert len(played) == 1
    assert played.iloc[0]["odds_h"] == 1.9      # oran korunmalı


def test_subscriber_management(db):
    assert db.subscribers() == []
    db.add_subscriber(111)
    db.add_subscriber(111)          # tekrar eklemek çoğaltmamalı
    db.add_subscriber(222)
    assert sorted(db.subscribers()) == [111, 222]
    assert db.is_subscriber(111)
    assert not db.is_subscriber(999)

    db.mark_sent(111)
    with db.connect() as conn:
        row = conn.execute("SELECT last_sent FROM subscribers WHERE chat_id=111").fetchone()
    assert row["last_sent"]

    db.remove_subscriber(111)
    assert db.subscribers() == [222]
    db.remove_subscriber(999)       # olmayanı silmek hata vermemeli


def test_upsert_fixtures_keeps_opening_odds_and_updates_current(db):
    """Oran hareketini ölçebilmek için ilk görülen oran korunmalı.

    Yaklaşan maçlarda kaynak yalnızca "şu anki" oranı verir; açılış/kapanış
    ayrımını kendi anlık görüntülerimizle kurarız.
    """
    fixture = {"league": "T1", "date": "2026-09-10", "home": "A", "away": "B",
               "source": "x"}
    db.upsert_fixtures([dict(fixture, odds_h=2.00, odds_d=3.40, odds_a=3.80)])
    db.upsert_fixtures([dict(fixture, odds_h=1.85, odds_d=3.50, odds_a=4.20)])
    db.upsert_fixtures([dict(fixture, odds_h=1.75, odds_d=3.60, odds_a=4.60)])

    row = db.load_fixtures(days=400).iloc[0]
    assert row["odds_h"] == 2.00        # açılış korunur
    assert row["codds_h"] == 1.75       # güncel tazelenir
    assert row["odds_a"] == 3.80 and row["codds_a"] == 4.60


def test_upsert_fixtures_skips_incomplete_rows(db):
    assert db.upsert_fixtures([{"league": "T1", "home": "A"}]) == 0
    assert db.upsert_fixtures([]) == 0


def test_coverage_reports_configured_but_empty_leagues(db):
    import json

    db.upsert_matches([base_row()])
    db.set_meta("missing_leagues", json.dumps(["T2"]))
    coverage = db.coverage(["T1", "T2", "E0"])
    assert coverage["present"] == {"T1": 1}
    assert coverage["empty"] == ["T2", "E0"]
    assert coverage["missing_from_source"] == ["T2"]
