"""Sınıf kalibrasyonu ve oran hareketi düzeltmesi."""

import numpy as np
import pytest

from sportoto.models.calibration import VectorScaling
from sportoto.models.market import DriftAdjustment


def biased_dataset(seed=0, n=4000, draw_factor=0.75):
    """Beraberliği sistematik olarak kaydıran bir model çıktısı üretir."""
    rng = np.random.default_rng(seed)
    true = rng.dirichlet([4, 3, 3], n)
    y = np.array([rng.choice(3, p=row) for row in true])
    biased = true.copy()
    biased[:, 1] *= draw_factor
    biased /= biased.sum(axis=1, keepdims=True)
    return true, biased, y


def log_loss(probs, y):
    return -np.mean(np.log(np.clip(probs[np.arange(len(y)), y], 1e-12, 1.0)))


# --- vektör ölçekleme ---
def test_calibration_corrects_systematic_draw_bias():
    """Beraberlik kayması Spor Toto'da doğrudan kupona yansır; düzeltilmeli."""
    true, biased, y = biased_dataset()
    calibrator = VectorScaling().fit(biased, y)
    assert calibrator.fitted

    corrected = calibrator.apply(biased)
    assert log_loss(corrected, y) < log_loss(biased, y)
    # Beraberlik payı gerçeğe yaklaşmalı
    assert abs(corrected[:, 1].mean() - true[:, 1].mean()) < abs(
        biased[:, 1].mean() - true[:, 1].mean()
    )


@pytest.mark.parametrize("seed", range(4))
def test_calibration_declines_when_there_is_no_bias(seed):
    """Kayma yoksa devreye girmemeli — gürültüden kazanç uydurmak kolaydır."""
    true, _, y = biased_dataset(seed=seed)
    assert not VectorScaling().fit(true, y).fitted


def test_calibration_is_identity_when_not_fitted():
    probs = np.array([[0.5, 0.3, 0.2], [0.2, 0.3, 0.5]])
    np.testing.assert_allclose(VectorScaling().apply(probs), probs, atol=1e-12)


def test_calibration_needs_enough_rows():
    _, biased, y = biased_dataset(n=100)
    assert not VectorScaling().fit(biased, y).fitted


def test_calibration_output_is_a_valid_distribution():
    _, biased, y = biased_dataset()
    corrected = VectorScaling().fit(biased, y).apply(biased)
    np.testing.assert_allclose(corrected.sum(axis=1), 1.0, atol=1e-9)
    assert (corrected > 0).all()


def test_calibration_round_trips_through_dict():
    _, biased, y = biased_dataset()
    calibrator = VectorScaling().fit(biased, y)
    restored = VectorScaling.from_dict(calibrator.to_dict())
    np.testing.assert_allclose(restored.apply(biased), calibrator.apply(biased))


# --- oran hareketi ---
def drift_dataset(seed=0, n=6000, travelled=0.7):
    """Piyasa gerçeğe doğru hareket eder ama yolun bir kısmını gider."""
    rng = np.random.default_rng(seed)
    true = rng.dirichlet([4, 3, 3], n)
    y = np.array([rng.choice(3, p=row) for row in true])
    opening = true * np.exp(rng.normal(0, 0.25, (n, 3)))
    opening /= opening.sum(axis=1, keepdims=True)
    closing = np.exp(np.log(opening) + travelled * (np.log(true) - np.log(opening)))
    closing /= closing.sum(axis=1, keepdims=True)
    return true, opening, closing, y


def test_drift_extrapolates_incomplete_market_movement():
    """Piyasa yönü doğru ama yolu tamamlamadıysa hareketi uzatmak kazandırır."""
    true, opening, closing, y = drift_dataset()
    drift = DriftAdjustment().fit(closing, opening, y)
    assert drift.fitted
    assert drift.gamma > 0
    adjusted = drift.apply(closing, opening)
    assert log_loss(adjusted, y) < log_loss(closing, y)


def test_drift_declines_when_movement_carries_no_information():
    _, _, closing, y = drift_dataset()
    assert not DriftAdjustment().fit(closing, closing.copy(), y).fitted


def test_drift_falls_back_to_closing_when_opening_missing():
    """Yaklaşan maçta henüz hareket olmayabilir; sistem durmamalı."""
    _, opening, closing, y = drift_dataset()
    drift = DriftAdjustment().fit(closing, opening, y)
    missing = np.full_like(opening, np.nan)
    np.testing.assert_allclose(drift.apply(closing, missing), closing)


def test_drift_handles_partially_missing_opening():
    _, opening, closing, y = drift_dataset()
    drift = DriftAdjustment().fit(closing, opening, y)
    assert drift.fitted
    partial = opening.copy()
    partial[::2] = np.nan
    out = drift.apply(closing, partial)
    np.testing.assert_allclose(out[::2], closing[::2])       # hareketsizler kapanışta
    assert not np.allclose(out[1::2], closing[1::2])          # diğerleri düzeltilmiş
    np.testing.assert_allclose(out.sum(axis=1), 1.0, atol=1e-9)


def test_drift_needs_enough_rows():
    _, opening, closing, y = drift_dataset(n=200)
    assert not DriftAdjustment().fit(closing, opening, y).fitted


def test_drift_round_trips_through_dict():
    _, opening, closing, y = drift_dataset()
    drift = DriftAdjustment().fit(closing, opening, y)
    restored = DriftAdjustment.from_dict(drift.to_dict())
    np.testing.assert_allclose(restored.apply(closing, opening), drift.apply(closing, opening))
