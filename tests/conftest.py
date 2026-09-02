import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sportoto.config import load_settings          # noqa: E402
from sportoto.sources.synthetic import generate_league  # noqa: E402
from sportoto.storage import Database              # noqa: E402


@pytest.fixture
def settings(tmp_path):
    s = load_settings(source="synthetic")
    s.data_dir = tmp_path
    s.db_path = tmp_path / "test.db"
    s.ensure_dirs()
    return s


@pytest.fixture
def synthetic_db(settings):
    db = Database(settings.db_path)
    rows = []
    for i, code in enumerate(["SYN1", "SYN2"]):
        rows += generate_league(n_teams=16, n_seasons=5, seed=21 + i, league=code)
    db.upsert_matches(rows)
    return db


@pytest.fixture
def synthetic_frame(settings, synthetic_db):
    from sportoto.pipeline import prepare_frame

    return prepare_frame(synthetic_db.load_matches(), settings)
