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


# --- haftalık fikstür akışı ---
def test_upcoming_predicts_stored_fixtures(trained):
    """Veritabanına yazılan fikstürler tahmin edilmeli ve tarih taşımalı."""
    from datetime import date, timedelta

    settings, predictor, _ = trained
    soon = date.today() + timedelta(days=2)
    predictor.db.upsert_matches([
        {"league": "SYN1", "date": soon.isoformat(), "home": "Team A", "away": "Team B",
         "odds_h": 1.85, "odds_d": 3.60, "odds_a": 4.20, "source": "test"},
        {"league": "SYN1", "date": soon.isoformat(), "home": "Team C", "away": "Team D",
         "source": "test"},
    ])
    try:
        predictions = predictor.upcoming(days=8)
        assert len(predictions) == 2
        for p in predictions:
            assert p.date == soon.isoformat()
            assert sum(p.probs) == pytest.approx(1.0, abs=1e-9)
            assert 0.0 < p.confidence <= 1.0
        # Oranı olan maçta piyasa bileşeni devreye girmeli, olmayanda girmemeli.
        with_odds = next(p for p in predictions if p.home == "Team A")
        without = next(p for p in predictions if p.home == "Team C")
        assert "market" in with_odds.components
        assert "market" not in without.components
    finally:
        with predictor.db.connect() as conn:
            conn.execute("DELETE FROM matches WHERE source = 'test'")


def test_upcoming_returns_empty_without_fixtures(trained):
    _, predictor, _ = trained
    assert predictor.upcoming(days=8) == []


def test_training_reports_out_of_sample_quality(trained):
    """Kullanıcıya gösterilen başarı yüzdesi ölçülmüş ve makul olmalı."""
    _, predictor, report = trained
    quality = report.get("quality", {})
    assert quality, "eğitim örnek dışı başarı ölçmeli"
    assert quality["n"] >= 100
    assert 0.30 < quality["favourite_hit_rate"] < 0.80
    assert quality["favourite_hit_rate"] > quality["baseline_hit_rate"]
    assert 0.0 < quality["rps"] < 0.5
    assert predictor.quality == quality


def test_weekly_report_renders(trained):
    from sportoto.predictor import MatchPrediction
    from sportoto.report import confidence_label, format_quality, format_weekly

    predictions = [
        MatchPrediction("Galatasaray", "Fenerbahce", "T1", 0.55, 0.25, 0.20,
                        date="2026-09-05"),
        MatchPrediction("Besiktas", "Trabzonspor", "T1", 0.30, 0.28, 0.42,
                        date="2026-09-06"),
    ]
    text = format_weekly(predictions)
    assert "Galatasaray" in text
    assert "5 Eylül" in text and "6 Eylül" in text
    assert "beklenen doğru sayısı" in text
    # 0.55 + 0.42 = 0.97 -> "1.0 / 2"
    assert "1.0 / 2" in text

    assert "Yaklaşan maç bulunamadı" in format_weekly([])
    assert "henüz ölçülmedi" in format_quality({})
    assert confidence_label(0.9) == "çok güçlü"
    assert confidence_label(0.33) == "belirsiz"


def test_quality_survives_blend_reuse(trained):
    """Kayıtlı kalibrasyon yeniden kullanılırken başarı ölçümü kaybolmamalı."""
    settings, predictor, report = trained
    predictor.save_blend()

    reloaded = Predictor(settings)
    assert reloaded.load_blend()
    reloaded.train(calibrate=False, progress=False)
    assert reloaded.quality
    assert reloaded.quality["favourite_hit_rate"] == pytest.approx(
        report["quality"]["favourite_hit_rate"]
    )


# --- satır hizalama: aynı tarihli maçlar karışmamalı ---
def test_predictions_stay_aligned_with_input_order(trained):
    """Her tahmin, kendi maçına ait olmalı.

    `prepare_frame` tarihe göre sıralama yapar ve bir kuponun 15 maçı aynı
    tarihi paylaşır. Satırlar konumla geri okunursa sıra karışır ve tahminler
    sessizce yanlış maça bağlanır — kullanıcı bunu fark edemez.
    """
    _, predictor, _ = trained
    teams = [chr(65 + i) for i in range(16)]
    fixtures = [
        {"home": f"Team {teams[i]}", "away": f"Team {teams[(i + 5) % 16]}", "league": "SYN1"}
        for i in range(15)
    ]
    predictions = predictor.predict(fixtures)
    assert len(predictions) == 15
    for fixture, prediction in zip(fixtures, predictions):
        assert prediction.home == fixture["home"]
        assert prediction.matched_home == fixture["home"]
        assert prediction.matched_away == fixture["away"]

    # Tahminler birbirinden farklı olmalı; hepsi aynıysa hizalama şüphelidir.
    assert len({round(p.p_home, 6) for p in predictions}) > 5


def test_predictions_are_order_independent(trained):
    """Aynı maç, listede nerede olursa olsun aynı tahmini almalı."""
    _, predictor, _ = trained
    base = [
        {"home": "Team A", "away": "Team B", "league": "SYN1"},
        {"home": "Team C", "away": "Team D", "league": "SYN1"},
        {"home": "Team E", "away": "Team F", "league": "SYN1"},
    ]
    forward = predictor.predict(base)
    backward = predictor.predict(list(reversed(base)))
    for a, b in zip(forward, reversed(backward)):
        assert a.home == b.home
        assert a.p_home == pytest.approx(b.p_home, abs=1e-9)


def test_unrecognised_team_is_not_silently_substituted(trained):
    """Bulanık arama her zaman bir aday döndürür; çok uzak eşleşme kabul edilmemeli.

    Aksi hâlde model, tamamen alakasız bir takımın gücüyle güvenli görünen bir
    tahmin üretir ve kullanıcı bunu fark edemez.
    """
    _, predictor, _ = trained
    prediction = predictor.predict(
        [{"home": "Zzz Kulubu XYZ", "away": "Qqq Spor WWW", "league": "YOK"}]
    )[0]

    # Takım adı değiştirilmemiş olmalı (rastgele bir takıma bağlanmamalı).
    assert prediction.matched_home == "Zzz Kulubu XYZ"
    assert prediction.matched_away == "Qqq Spor WWW"
    assert any("tanınmadı" in w for w in prediction.warnings)
    # Gol modeli bu maça uygulanamaz; satır zayıf olarak işaretlenmeli.
    assert "dc" not in prediction.components
    assert prediction.low_data


def test_known_teams_are_fully_supported(trained):
    _, predictor, _ = trained
    prediction = predictor.predict(
        [{"home": "Team A", "away": "Team B", "league": "SYN1"}]
    )[0]
    assert not prediction.no_data
    assert not prediction.low_data
    assert "dc" in prediction.components


def test_no_data_rows_render_as_unknown_not_as_a_prediction():
    """Hiç bileşen yoksa çıktı %33/%33/%33'ü tahmin gibi göstermemeli."""
    from sportoto.predictor import MatchPrediction
    from sportoto.report import format_predictions_mobile

    text = format_predictions_mobile(
        [MatchPrediction("A", "B", None, 1 / 3, 1 / 3, 1 / 3, no_data=True)]
    )
    assert "veri yok" in text
    assert "%33" not in text


# --- mobil çıktı ---
def test_mobile_renderers_are_readable_and_escape_html():
    import re

    from sportoto.coupon.optimizer import optimize_coupon
    from sportoto.predictor import MatchPrediction
    from sportoto.report import (
        format_coupon_mobile,
        format_frontier_mobile,
        format_predictions_mobile,
        format_tables_mobile,
        format_weekly_mobile,
    )
    from sportoto.coupon.optimizer import budget_frontier

    predictions = [
        MatchPrediction("Galatasaray", "Fenerbahçe", "T1", 0.52, 0.26, 0.22,
                        date="2026-09-05", components={"dc": (0.5, 0.26, 0.24)}),
        MatchPrediction("A & B <script>", "C", "T1", 1 / 3, 1 / 3, 1 / 3,
                        date="2026-09-05", no_data=True),
    ]
    text = format_predictions_mobile(predictions)
    assert "Galatasaray" in text
    assert "veri yok" in text                      # no_data açıkça belirtilmeli
    assert "<script>" not in text                  # HTML kaçışı
    assert "&lt;script&gt;" in text

    weekly = format_weekly_mobile(predictions)
    assert "5 Eylül" in weekly and "veri yok" in weekly
    assert "Yaklaşan maç bulunamadı" in format_weekly_mobile([])

    plan = optimize_coupon((predictions * 8)[:15], max_columns=48, column_price=5.0)
    coupon = format_coupon_mobile(plan)
    assert "işaretle" in coupon
    assert "kolon" in coupon
    # Spor Toto yazımı: çoklu işaretler tire ile
    assert "1-0" in coupon or "1-0-2" in coupon

    frontier = format_frontier_mobile(
        budget_frontier((predictions * 8)[:15], column_price=5.0, max_columns=200), 5.0
    )
    assert "kolon" in frontier
    assert format_frontier_mobile([], 5.0)

    tables = format_tables_mobile(5.0)
    assert "24" in tables and "çift" in tables
    # Dar ekran için: tablo satırları makul genişlikte kalmalı
    plain = [re.sub(r"<[^>]+>", "", line) for line in tables.splitlines()]
    assert max(len(line) for line in plain if line.startswith("   ")) < 60


# --- saklanan oranların yapıştırılan kupona bağlanması ---
def test_pasted_coupon_picks_up_stored_odds(trained):
    """Elle yapıştırılan listede oran yoktur; veritabanındaki fikstürden alınmalı.

    Piyasa modelin en güçlü tek sinyalidir ve `ingest` onu zaten indirir;
    kullanmamak en büyük tek kalite kaybı olurdu.
    """
    from datetime import date, timedelta

    _, predictor, _ = trained
    soon = date.today() + timedelta(days=3)
    predictor.db.upsert_matches([
        {"league": "SYN1", "date": soon.isoformat(), "home": "Team A", "away": "Team B",
         "odds_h": 1.40, "odds_d": 4.80, "odds_a": 7.50, "source": "test"},
    ])
    try:
        # Oran verilmeden, yalnızca takım adlarıyla
        prediction = predictor.predict([{"home": "Team A", "away": "Team B"}])[0]
        assert "market" in prediction.components, "saklanan oran kullanılmadı"
        assert prediction.match_id                     # fikstüre bağlandı
        assert prediction.date == soon.isoformat()     # gerçek tarih alındı
        assert any("bahis oranları kullanıldı" in w for w in prediction.warnings)
        # Kısa oran (1.40) ev sahibini belirgin favori yapmalı
        assert prediction.p_home > 0.55
    finally:
        with predictor.db.connect() as conn:
            conn.execute("DELETE FROM matches WHERE source = 'test'")


def test_explicit_odds_override_stored_ones(trained):
    from datetime import date, timedelta

    _, predictor, _ = trained
    soon = date.today() + timedelta(days=3)
    predictor.db.upsert_matches([
        {"league": "SYN1", "date": soon.isoformat(), "home": "Team A", "away": "Team B",
         "odds_h": 1.40, "odds_d": 4.80, "odds_a": 7.50, "source": "test"},
    ])
    try:
        given = predictor.predict([
            {"home": "Team A", "away": "Team B",
             "odds_h": 9.00, "odds_d": 5.00, "odds_a": 1.35}
        ])[0]
        assert given.p_away > given.p_home       # verilen oran kazanmalı
    finally:
        with predictor.db.connect() as conn:
            conn.execute("DELETE FROM matches WHERE source = 'test'")


# --- gerçek karne ---
def test_track_record_scores_saved_predictions_against_results(trained):
    """Kaydedilen tahminler, maç oynanınca gerçek sonuçla karşılaştırılmalı."""
    from datetime import date, timedelta

    _, predictor, _ = trained
    db = predictor.db
    with db.connect() as conn:
        conn.execute("DELETE FROM predictions")

    assert predictor.track_record()["n"] == 0

    soon = date.today() + timedelta(days=2)
    db.upsert_matches([
        {"league": "SYN1", "date": soon.isoformat(), "home": f"Team {c}",
         "away": "Team P", "source": "test"}
        for c in "ABCDEFGH"
    ])
    try:
        predictions = predictor.upcoming(days=8)
        assert len(predictions) >= 8
        assert predictor.record(predictions) >= 8
        # Henüz oynanmadı: karne boş ama bekleyen sayısı dolu
        record = predictor.track_record()
        assert record["n"] == 0
        assert record["pending"] >= 8

        # Sonuçlar gelsin
        db.upsert_matches([
            {"league": "SYN1", "date": soon.isoformat(), "home": f"Team {c}",
             "away": "Team P", "fthg": 2, "ftag": 0, "ftr": "H", "source": "test"}
            for c in "ABCDEFGH"
        ])
        record = predictor.track_record()
        assert record["n"] >= 8
        assert 0.0 <= record["favourite_hit_rate"] <= 1.0
        assert record["first_date"] and record["last_date"]
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM matches WHERE source = 'test'")
            conn.execute("DELETE FROM predictions")


def test_predictions_without_match_id_are_not_recorded(trained):
    """Fikstüre bağlanamayan tahmin kaydedilmemeli — sonucu asla eşleşmez."""
    _, predictor, _ = trained
    with predictor.db.connect() as conn:
        conn.execute("DELETE FROM predictions")
    predictions = predictor.predict([{"home": "Team A", "away": "Team B", "league": "SYN1"}])
    assert predictions[0].match_id is None
    assert predictor.record(predictions) == 0


def test_track_record_report_renders():
    from sportoto.report import format_track_record

    assert "Henüz sonucu belli olan" in format_track_record({"n": 0, "pending": 3})
    text = format_track_record({
        "n": 120, "pending": 15, "first_date": "2026-01-01", "last_date": "2026-05-01",
        "favourite_hit_rate": 0.533, "rps": 0.201, "log_loss": 0.988,
        "bands": [{"label": "güçlü", "n": 40, "hit_rate": 0.70, "claimed": 0.68}],
    })
    assert "GERÇEK KARNE" in text
    assert "%53,3" in text
    assert "güçlü" in text
