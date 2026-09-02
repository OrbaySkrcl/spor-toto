"""Uçtan uca: veri toplama → eğitim → tahmin → kupon → backtest."""

import numpy as np
import pytest

from sportoto.backtest import (
    metrics,
    ranked_probability_score,
    run_backtest,
    simulate_coupons,
)
from sportoto.coupon.optimizer import optimize_coupon
from sportoto.ingest import ingest
from sportoto.predictor import Predictor
from sportoto.report import format_coupon, format_predictions, format_stats, format_tables


def test_ingest_is_idempotent(settings):
    first = ingest(settings, leagues=["SYN"], with_fixtures=False)
    second = ingest(settings, leagues=["SYN"], with_fixtures=False)
    assert first.written == second.written > 0
    from sportoto.storage import Database

    assert Database(settings.db_path).stats()["matches"] == first.written


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    """Bir kez eğitilen tahminci — eğitim pahalı olduğu için modül kapsamında."""
    from sportoto.config import load_settings
    from sportoto.sources.synthetic import generate_league
    from sportoto.storage import Database

    tmp = tmp_path_factory.mktemp("e2e")
    settings = load_settings(source="synthetic")
    settings.data_dir = tmp
    settings.db_path = tmp / "e2e.db"
    settings.ensure_dirs()

    db = Database(settings.db_path)
    rows = []
    for i, code in enumerate(["SYN1", "SYN2"]):
        rows += generate_league(n_teams=16, n_seasons=6, seed=31 + i, league=code)
    db.upsert_matches(rows)

    predictor = Predictor(settings)
    report = predictor.train(calibrate=True, refit_days=90, calibration_days=540, progress=False)
    return settings, predictor, report


def test_training_produces_a_calibrated_blend(trained):
    _, predictor, report = trained
    assert report["matches"] > 1000
    assert predictor.blend.components
    assert sum(predictor.blend.weights.values()) == pytest.approx(1.0, abs=1e-6)
    assert predictor.blend.temp_min <= predictor.blend.temperature <= predictor.blend.temp_max
    # Sentetik oranlar gerçek olasılıklardan üretildiği için piyasa baskın olmalı.
    assert predictor.blend.weights.get("market", 0.0) > 0.3


def test_predict_returns_valid_probabilities(trained):
    _, predictor, _ = trained
    fixtures = [
        {"home": "Team A", "away": "Team B", "league": "SYN1"},
        {"home": "Team C", "away": "Team D", "league": "SYN1"},
    ]
    predictions = predictor.predict(fixtures)
    assert len(predictions) == 2
    for p in predictions:
        assert sum(p.probs) == pytest.approx(1.0, abs=1e-9)
        assert all(0.0 < v < 1.0 for v in p.probs)
        assert p.favourite in {"1", "0", "2"}
        assert 0.0 <= p.entropy <= 1.0
        assert p.components  # en az bir bileşen katkı vermeli


def test_predict_flags_unknown_team(trained):
    _, predictor, _ = trained
    predictions = predictor.predict([{"home": "Team A", "away": "Kayıp Kulüp XYZ"}])
    assert predictions[0].warnings


def test_predict_with_supplied_odds_uses_market(trained):
    """Kullanıcı oran verirse piyasa bileşeni devreye girmeli."""
    _, predictor, _ = trained
    without = predictor.predict([{"home": "Team A", "away": "Team B", "league": "SYN1"}])[0]
    with_odds = predictor.predict(
        [{"home": "Team A", "away": "Team B", "league": "SYN1",
          "odds_h": 1.30, "odds_d": 5.50, "odds_a": 9.00}]
    )[0]
    assert "market" not in without.components
    assert "market" in with_odds.components
    assert with_odds.p_home > without.p_home


def test_full_coupon_flow(trained):
    settings, predictor, _ = trained
    teams = [chr(65 + i) for i in range(16)]
    fixtures = [
        {"home": f"Team {teams[i]}", "away": f"Team {teams[(i + 8) % 16]}", "league": "SYN1"}
        for i in range(15)
    ]
    predictions = predictor.predict(fixtures)
    assert len(predictions) == 15

    plan = optimize_coupon(predictions, budget=1000.0, column_price=settings.coupon.column_price)
    assert plan.cost <= 1000.0
    assert len(plan.selections) == 15
    assert 0.0 < plan.p_all_correct < 1.0
    assert sum(plan.distribution.values()) == pytest.approx(1.0, abs=1e-9)

    # Rapor biçimlendiricileri patlamamalı ve maç adlarını içermeli.
    text = format_predictions(predictions) + "\n" + format_coupon(plan)
    assert "Team A" in text
    assert "P(15/15)" in text


def test_blend_persists_across_instances(trained):
    settings, predictor, _ = trained
    path = predictor.save_blend()
    assert path.exists()

    reloaded = Predictor(settings)
    assert reloaded.load_blend()
    assert reloaded.blend.weights == predictor.blend.weights
    assert reloaded.blend.components == predictor.blend.components


def test_backtest_runs_and_beats_uniform_baseline(trained):
    settings, _, _ = trained
    from sportoto.storage import Database

    result = run_backtest(
        settings, db=Database(settings.db_path),
        start="2021-01-01", refit_days=90, calibration_days=540,
        budgets=[1, 96], progress=False,
    )
    assert result.overall["n"] > 200
    # Düzgün dağılımın log-loss'u ln(3) ≈ 1.0986; model bunu net geçmeli.
    assert result.overall["log_loss"] < 1.05
    assert result.overall["accuracy"] > 0.40
    assert 0.0 <= result.overall["rps"] <= 1.0
    assert len(result.simulations) == 2
    assert "BACKTEST" in result.report()
    assert not result.calibration.empty


def test_predict_before_training_raises(settings):
    with pytest.raises(RuntimeError):
        Predictor(settings).predict([{"home": "A", "away": "B"}])


def test_train_on_empty_database_raises(settings):
    with pytest.raises(RuntimeError):
        Predictor(settings).train()


# --- metrikler ---
def test_ranked_probability_score_penalises_distant_errors():
    """RPS, 1 derken 2 çıkmasını 1 derken 0 çıkmasından daha ağır cezalandırmalı."""
    certain_home = np.array([[1.0, 0.0, 0.0]])
    draw_happened = ranked_probability_score(certain_home, np.array([1]))
    away_happened = ranked_probability_score(certain_home, np.array([2]))
    assert away_happened > draw_happened


def test_perfect_prediction_scores_zero():
    probs = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    y = np.array([0, 2])
    m = metrics(probs, y)
    assert m["accuracy"] == 1.0
    assert m["rps"] == pytest.approx(0.0)
    assert m["brier"] == pytest.approx(0.0)


def test_simulate_coupons_handles_short_input():
    import pandas as pd

    rows = pd.DataFrame(
        {"date": pd.to_datetime(["2024-01-01"] * 5), "y": [0, 1, 2, 0, 1],
         "p_home": 0.4, "p_draw": 0.3, "p_away": 0.3}
    )
    assert simulate_coupons(rows, [24], 5.0) == []


def test_report_helpers_render(settings):
    assert "ÜRETİLEN KOLON ADEDİ" in format_tables(5.0)
    stats = {"matches": 10, "first_date": "2024-01-01", "last_date": "2024-05-01",
             "leagues": 2, "with_odds": 5, "per_league": [{"league": "T1", "n": 10, "last": "x"}]}
    assert "VERİ DURUMU" in format_stats(stats)


def test_manual_adjustment_changes_prediction(trained):
    """Sakatlık düzeltmesi tahmine gerçekten yansımalı.

    `adjustments` tablosu yalnızca yazılıp okunuyor olsaydı bu test geçmezdi;
    düzeltmenin Dixon-Coles gol beklentisine ulaştığını doğrular.
    """
    from datetime import timedelta

    settings, predictor, _ = trained
    fixture = [{"home": "Team A", "away": "Team B", "league": "SYN1"}]
    before = predictor.predict(fixture)[0]

    match_date = predictor.frame["date"].max() + timedelta(days=1)
    predictor.db.add_adjustment(
        "Team A",
        (match_date - timedelta(days=1)).date().isoformat(),
        attack=-0.60,
        valid_to=(match_date + timedelta(days=7)).date().isoformat(),
        note="test: golcü yok",
    )
    try:
        after = predictor.predict(fixture)[0]
        assert after.p_home < before.p_home - 0.01
        assert after.components["dc"][0] < before.components["dc"][0]
    finally:
        with predictor.db.connect() as conn:
            conn.execute("DELETE FROM adjustments WHERE team = 'Team A'")


def test_adjustment_outside_validity_window_is_ignored(trained):
    from datetime import timedelta

    settings, predictor, _ = trained
    fixture = [{"home": "Team A", "away": "Team B", "league": "SYN1"}]
    before = predictor.predict(fixture)[0]

    match_date = predictor.frame["date"].max() + timedelta(days=1)
    predictor.db.add_adjustment(
        "Team A",
        (match_date - timedelta(days=90)).date().isoformat(),
        attack=-0.60,
        valid_to=(match_date - timedelta(days=30)).date().isoformat(),
    )
    try:
        after = predictor.predict(fixture)[0]
        assert after.p_home == pytest.approx(before.p_home, abs=1e-12)
    finally:
        with predictor.db.connect() as conn:
            conn.execute("DELETE FROM adjustments WHERE team = 'Team A'")
