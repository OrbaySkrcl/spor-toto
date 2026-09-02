"""Oran → olasılık dönüşümü (marj temizleme)."""

import math

import pytest

from sportoto.models.market import implied_probabilities, overround, remove_margin


@pytest.mark.parametrize("method", ["basic", "power", "shin"])
def test_probabilities_sum_to_one(method):
    for odds in ([1.90, 3.50, 4.20], [1.20, 7.0, 15.0], [3.0, 3.2, 2.6], [1.05, 15.0, 40.0]):
        probs = remove_margin(odds, method)
        assert math.isclose(sum(probs), 1.0, abs_tol=1e-9)
        assert all(0.0 < p < 1.0 for p in probs)


@pytest.mark.parametrize("method", ["basic", "power", "shin"])
def test_ordering_follows_odds(method):
    """Kısa oran daha yüksek olasılık almalı."""
    probs = remove_margin([1.50, 4.00, 7.00], method)
    assert probs[0] > probs[1] > probs[2]


def test_power_reduces_favourite_longshot_bias():
    """Power yöntemi uzun kuyruğa naive normalizasyondan daha az olasılık vermeli.

    Kitapçı marjı orantısız olarak yüksek oranlara yüklenir; bunu düzeltmeyen
    `basic` yöntem sürpriz sonucu sistematik olarak abartır.
    """
    odds = [1.25, 6.00, 13.00]
    basic = remove_margin(odds, "basic")
    power = remove_margin(odds, "power")
    assert power[2] < basic[2]
    assert power[0] > basic[0]


def test_no_margin_passes_through():
    """Marjı olmayan (adil) oranlarda dönüşüm neredeyse kimlik olmalı."""
    fair = [1 / 0.5, 1 / 0.3, 1 / 0.2]
    probs = remove_margin(fair, "power")
    assert probs == pytest.approx([0.5, 0.3, 0.2], abs=1e-6)


def test_overround_calculation():
    assert overround(2.0, 4.0, 4.0) == pytest.approx(0.0, abs=1e-9)
    assert overround(1.90, 3.50, 4.20) > 0


def test_implied_probabilities_rejects_bad_input():
    assert implied_probabilities(None, 3.5, 4.2) is None
    assert implied_probabilities(1.0, 3.5, 4.2) is None      # oran 1.0 geçersiz
    assert implied_probabilities(float("nan"), 3.5, 4.2) is None
    assert implied_probabilities(1.9, 3.5, 4.2) is not None


def test_unknown_method_raises():
    with pytest.raises(ValueError):
        remove_margin([1.9, 3.5, 4.2], "sihir")
