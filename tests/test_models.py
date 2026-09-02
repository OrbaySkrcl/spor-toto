"""Dixon-Coles, sıralı Elo, blend ve form modeli."""

import numpy as np
import pandas as pd
import pytest
from sportoto.models.blend import LogPoolBlend
from sportoto.models.dixon_coles import DixonColes
from sportoto.models.elo_model import EloOrdinal
from sportoto.sources.synthetic import generate_league


@pytest.fixture(scope="module")
def synthetic_matches():
    rows = generate_league(n_teams=14, n_seasons=4, seed=5)
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df


def test_dixon_coles_analytic_gradient_matches_numeric(synthetic_matches):
    """Analitik gradyan yanlışsa optimizasyon sessizce yanlış yere yakınsar."""
    dc = DixonColes(xi=0.0018, ridge=0.02)
    problem = dc.build_objective(synthetic_matches)
    objective, x0 = problem["objective"], problem["x0"]

    rng = np.random.default_rng(0)
    x = x0 + rng.normal(0, 0.15, size=x0.shape)
    x[-1] = np.clip(x[-1], -0.2, 0.2)

    _, analytic = objective(x)
    numeric = np.zeros_like(x)
    eps = 1e-6
    for i in range(len(x)):
        up, down = x.copy(), x.copy()
        up[i] += eps
        down[i] -= eps
        numeric[i] = (objective(up)[0] - objective(down)[0]) / (2 * eps)

    assert np.abs(analytic - numeric).max() < 1e-4 * max(1.0, np.abs(numeric).max())


def test_dixon_coles_recovers_synthetic_strengths(synthetic_matches):
    """Sentetik veri bilinen güçlerden üretildi; model sıralamayı yakalamalı."""
    dc = DixonColes(xi=0.0)  # zaman ağırlığı yok: tüm sezonlar eşit
    fit = dc.fit(synthetic_matches)
    assert fit.converged

    # Gerçek gol ortalaması ile model atak sıralaması uyumlu olmalı.
    scored = (
        synthetic_matches.groupby("home")["fthg"].mean()
        + synthetic_matches.groupby("away")["ftag"].mean()
    )
    model = pd.Series(fit.attack)
    common = scored.index.intersection(model.index)
    correlation = np.corrcoef(scored[common], model[common])[0, 1]
    assert correlation > 0.85


def test_dixon_coles_probabilities_are_valid(synthetic_matches):
    dc = DixonColes()
    fit = dc.fit(synthetic_matches)
    for home in fit.teams[:4]:
        for away in fit.teams[:4]:
            if home == away:
                continue
            probs = dc.outcome_probabilities(fit, home, away)
            assert sum(probs) == pytest.approx(1.0, abs=1e-9)
            assert all(0.0 < p < 1.0 for p in probs)


def test_dixon_coles_home_advantage_is_positive(synthetic_matches):
    fit = DixonColes().fit(synthetic_matches)
    assert fit.home_advantage > 0


def test_dixon_coles_reversing_venue_reverses_probabilities(synthetic_matches):
    dc = DixonColes()
    fit = dc.fit(synthetic_matches)
    strong = max(fit.attack, key=fit.attack.get)
    weak = min(fit.attack, key=fit.attack.get)
    at_home = dc.outcome_probabilities(fit, strong, weak)
    away = dc.outcome_probabilities(fit, weak, strong)
    assert at_home[0] > away[0]     # güçlü takım evinde daha olası kazanır
    assert away[2] > at_home[2]


def test_dixon_coles_unknown_team_falls_back_to_average(synthetic_matches):
    dc = DixonColes()
    fit = dc.fit(synthetic_matches)
    assert not fit.knows("Bilinmeyen FK")
    probs = dc.outcome_probabilities(fit, "Bilinmeyen FK", fit.teams[0])
    assert sum(probs) == pytest.approx(1.0, abs=1e-9)


def test_dixon_coles_needs_matches():
    with pytest.raises(ValueError):
        DixonColes().fit(pd.DataFrame(columns=["date", "home", "away", "fthg", "ftag"]))


# --- sıralı Elo ---
def test_elo_ordinal_is_monotone_in_rating_difference():
    """Elo farkı arttıkça P(1) artmalı, P(2) azalmalı.

    fit ve predict'in sınıf sıralaması ayrışırsa β işareti döner ve model
    sessizce ters olasılık üretir; bu test onu yakalar.
    """
    rng = np.random.default_rng(3)
    diff = rng.normal(0, 150, 4000)
    z = diff / 200.0
    probs = np.stack([
        1 / (1 + np.exp(-z + 0.4)),
        np.full_like(z, 0.26),
        1 / (1 + np.exp(z + 0.4)),
    ], axis=1)
    probs /= probs.sum(axis=1, keepdims=True)
    y = np.array([rng.choice(3, p=row) for row in probs])

    model = EloOrdinal().fit(diff, y)
    assert model.beta > 0

    grid = model.predict([-400, -200, 0, 200, 400])
    assert np.all(np.diff(grid[:, 0]) > 0)      # P(1) artan
    assert np.all(np.diff(grid[:, 2]) < 0)      # P(2) azalan
    assert grid.sum(axis=1) == pytest.approx(np.ones(5), abs=1e-9)


def test_elo_ordinal_draw_probability_peaks_at_parity():
    model = EloOrdinal(beta=0.7, theta_away=-0.5, theta_draw=0.5)
    probs = model.predict([-500, -200, 0, 200, 500])
    assert probs[2, 1] == max(probs[:, 1])


# --- blend ---
def test_blend_excludes_components_without_coverage():
    """Kalibrasyonda hiç görünmeyen bileşene ağırlık uydurulmamalı."""
    rng = np.random.default_rng(0)
    n = 900
    y = rng.integers(0, 3, n)
    informative = np.full((n, 3), 1 / 3)
    informative[np.arange(n), y] = 0.6
    informative /= informative.sum(axis=1, keepdims=True)

    blend = LogPoolBlend().fit(
        {
            "dc": informative,
            "elo": rng.dirichlet([2, 2, 2], n),
            "market": np.full((n, 3), np.nan),
        },
        y,
    )
    assert "market" not in blend.components
    assert blend.coverage["market"] == 0.0
    assert blend.weights["dc"] > blend.weights["elo"]


def test_blend_temperature_stays_within_bounds():
    rng = np.random.default_rng(1)
    n = 800
    y = rng.integers(0, 3, n)
    perfect = np.full((n, 3), 0.001)
    perfect[np.arange(n), y] = 0.998
    blend = LogPoolBlend().fit({"dc": perfect, "elo": rng.dirichlet([2, 2, 2], n)}, y)
    assert blend.temp_min <= blend.temperature <= blend.temp_max


def test_blend_handles_partially_missing_component():
    """Oranı olan ve olmayan maçlar aynı kuponda karışabilir."""
    rng = np.random.default_rng(2)
    n = 600
    y = rng.integers(0, 3, n)
    dc = rng.dirichlet([3, 3, 3], n)
    market = rng.dirichlet([3, 3, 3], n)
    market[::2] = np.nan                    # maçların yarısında oran yok

    blend = LogPoolBlend().fit({"dc": dc, "market": market}, y)
    assert "market" in blend.components
    probs = blend.predict({"dc": dc, "market": market})
    assert probs.sum(axis=1) == pytest.approx(np.ones(n), abs=1e-9)
    assert np.isfinite(probs).all()


def test_blend_round_trips_through_dict():
    rng = np.random.default_rng(4)
    n = 400
    y = rng.integers(0, 3, n)
    blend = LogPoolBlend().fit({"dc": rng.dirichlet([3, 3, 3], n)}, y)
    restored = LogPoolBlend.from_dict(blend.to_dict())
    assert restored.components == blend.components
    assert restored.weights == blend.weights
    assert restored.temperature == pytest.approx(blend.temperature)


def test_blend_predict_before_fit_raises():
    with pytest.raises(RuntimeError):
        LogPoolBlend().predict({"dc": np.full((3, 3), 1 / 3)})


# --- blend profilleri: eksik bileşen dayanıklılığı ---
def _profile_fixture(seed=7, n=1200):
    """dc her zaman, market yalnızca eğitimde bulunan bir veri seti üretir."""
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 3, n)
    def informative(strength):
        p = np.full((n, 3), (1.0 - strength) / 2.0)
        p[np.arange(n), y] = strength
        return p / p.sum(axis=1, keepdims=True)
    return {
        "dc": informative(0.55),
        "elo": informative(0.50),
        "market": informative(0.75),   # en güçlü sinyal, diğerlerini ezer
    }, y


def test_blend_fits_a_profile_for_each_component_subset():
    probs, y = _profile_fixture()
    blend = LogPoolBlend().fit(probs, y)
    from sportoto.models.blend import profile_key

    for subset in (["dc"], ["elo"], ["dc", "elo"], ["dc", "elo", "market"]):
        assert profile_key(subset) in blend.profiles, subset


def test_missing_component_does_not_collapse_to_uniform():
    """Oran yokken model susmamalı.

    Ağırlıklar oranların hep mevcut olduğu veride öğrenildiği için piyasa
    diğerlerini sıfıra ezer. Kullanıcı kupon yapıştırdığında oran yoktur;
    profiller olmasaydı havuz düzgün dağılıma düşer ve model bildiği maçlarda
    bile %33/%33/%33 derdi.
    """
    probs, y = _profile_fixture()
    blend = LogPoolBlend().fit(probs, y)
    assert blend.weights["market"] > blend.weights["dc"]      # eğitimde piyasa baskın

    n = len(y)
    without_market = {
        "dc": probs["dc"],
        "elo": probs["elo"],
        "market": np.full((n, 3), np.nan),
    }
    predicted = blend.predict(without_market)
    assert predicted.sum(axis=1) == pytest.approx(np.ones(n), abs=1e-9)
    spread = np.abs(predicted - 1.0 / 3.0).max(axis=1)
    assert (spread > 0.02).mean() > 0.95, "oran yokken tahminler tekdüzeye düşüyor"
    # Doğru sonuca verilen ortalama olasılık şansa göre belirgin yüksek olmalı.
    assert predicted[np.arange(n), y].mean() > 0.45


def test_weight_floor_keeps_every_component_usable():
    probs, y = _profile_fixture()
    blend = LogPoolBlend().fit(probs, y)
    assert min(blend.weights.values()) >= blend.weight_floor - 1e-9
    assert sum(blend.weights.values()) == pytest.approx(1.0, abs=1e-6)


def test_availability_reports_usable_components_per_row():
    probs, y = _profile_fixture(n=300)
    probs["market"][::2] = np.nan
    blend = LogPoolBlend().fit(probs, y)
    availability = blend.availability(probs)
    assert len(availability) == 300
    assert "market" not in availability[0]
    assert "market" in availability[1]
    assert "dc" in availability[0]


def test_rows_without_any_component_stay_uniform_and_are_detectable():
    probs, y = _profile_fixture(n=300)
    blend = LogPoolBlend().fit(probs, y)
    empty = {k: np.full((300, 3), np.nan) for k in probs}
    predicted = blend.predict(empty)
    assert predicted == pytest.approx(np.full((300, 3), 1 / 3), abs=1e-9)
    assert all(row == [] for row in blend.availability(empty))


def test_profiles_survive_dict_round_trip():
    probs, y = _profile_fixture(n=600)
    blend = LogPoolBlend().fit(probs, y)
    restored = LogPoolBlend.from_dict(blend.to_dict())
    assert restored.profiles == blend.profiles
    n = len(y)
    without = dict(probs, market=np.full((n, 3), np.nan))
    np.testing.assert_allclose(restored.predict(without), blend.predict(without))
