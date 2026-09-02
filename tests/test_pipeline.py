"""Boru hattı doğruluğu — özellikle geriye bakma (look-ahead) sızıntısı."""

import numpy as np
import pandas as pd

from sportoto.features.form import FORM_COLUMNS
from sportoto.pipeline import (
    component_probabilities,
    fit_components,
    outcome_index,
    prepare_frame,
    stack_components,
    walk_forward,
)

# Sentetik veri 2018-08 ile 2021-07 arasını kapsar; kesim ortada olmalı ki
# her iki yanda da anlamlı miktarda maç kalsın.
CUTOFF = pd.Timestamp("2020-06-01")


def test_outcome_index_mapping():
    assert list(outcome_index(["H", "D", "A", None])) == [0, 1, 2, -1]


def test_features_do_not_depend_on_future_matches(settings, synthetic_db):
    """Geleceği bozunca geçmişin özellikleri değişmemeli.

    Bu, sızıntının en doğrudan testi: `CUTOFF` sonrasındaki tüm skorları
    değiştirip özellikleri yeniden üretiyoruz. Herhangi bir özellik ileriye
    bakıyorsa, kesim öncesi satırlar da değişir.
    """
    raw = synthetic_db.load_matches()
    clean = prepare_frame(raw, settings)

    corrupted = raw.copy()
    future = corrupted["date"] >= CUTOFF
    assert future.sum() > 100, "test anlamlı olsun diye kesim sonrası veri gerekli"
    # Gelecekteki skorları ve oranları tamamen boz.
    corrupted.loc[future, "fthg"] = 9
    corrupted.loc[future, "ftag"] = 0
    corrupted.loc[future, "ftr"] = "H"
    corrupted.loc[future, ["odds_h", "odds_d", "odds_a"]] = 1.01
    dirty = prepare_frame(corrupted, settings)

    key = ["date", "home", "away"]
    a = clean[clean["date"] < CUTOFF].sort_values(key).reset_index(drop=True)
    b = dirty[dirty["date"] < CUTOFF].sort_values(key).reset_index(drop=True)
    assert len(a) == len(b) and len(a) > 100

    for column in ["elo_home", "elo_away", "elo_diff", "mkt_h", "mkt_d", "mkt_a", *FORM_COLUMNS]:
        np.testing.assert_allclose(
            a[column].to_numpy(dtype=float),
            b[column].to_numpy(dtype=float),
            rtol=1e-12,
            atol=1e-12,
            err_msg=f"'{column}' sütunu gelecekteki maçlardan etkileniyor",
        )


def test_walk_forward_predictions_ignore_future(settings, synthetic_db):
    """Yürüyen tahminler de gelecekten etkilenmemeli (model parametreleri dahil)."""
    raw = synthetic_db.load_matches()
    start = pd.Timestamp("2019-08-01")

    clean = prepare_frame(raw, settings)
    corrupted = raw.copy()
    future = corrupted["date"] >= CUTOFF
    corrupted.loc[future, "fthg"] = 9
    corrupted.loc[future, "ftag"] = 0
    corrupted.loc[future, "ftr"] = "H"
    dirty = prepare_frame(corrupted, settings)

    args = dict(settings=settings, start=start, end=CUTOFF - pd.Timedelta(days=1), refit_days=60)
    a = walk_forward(clean, **args).sort_values(["date", "home", "away"]).reset_index(drop=True)
    b = walk_forward(dirty, **args).sort_values(["date", "home", "away"]).reset_index(drop=True)

    assert len(a) > 50
    for column in ["dc_h", "dc_d", "dc_a", "elo_h", "elo_d", "elo_a", "form_h"]:
        np.testing.assert_allclose(
            a[column].to_numpy(dtype=float),
            b[column].to_numpy(dtype=float),
            rtol=1e-9, atol=1e-9,
            err_msg=f"'{column}' tahmini gelecekteki sonuçlardan etkileniyor",
        )


def test_prepare_frame_adds_expected_columns(synthetic_frame):
    for column in ["elo_diff", "mkt_h", "y", *FORM_COLUMNS]:
        assert column in synthetic_frame.columns
    assert synthetic_frame["y"].isin([0, 1, 2]).all()


def test_component_probabilities_are_valid(settings, synthetic_frame):
    as_of = pd.Timestamp("2021-01-01")
    history = synthetic_frame[synthetic_frame["date"] < as_of]
    batch = synthetic_frame[synthetic_frame["date"] >= as_of].head(50)

    models = fit_components(history, settings, as_of)
    probs = component_probabilities(models, batch, settings)

    assert set(probs) == {"dc", "elo", "market", "form"}
    for name, values in probs.items():
        assert values.shape == (len(batch), 3)
        usable = ~np.isnan(values).any(axis=1)
        assert usable.sum() > 0, f"{name} hiç tahmin üretmedi"
        np.testing.assert_allclose(values[usable].sum(axis=1), 1.0, atol=1e-9)


def test_dixon_coles_skips_teams_with_too_few_matches(settings, synthetic_frame):
    """Modele yeni girmiş takım için DC susmalı — yanlış güvenli tahmin üretmemeli."""
    as_of = pd.Timestamp("2021-01-01")
    history = synthetic_frame[synthetic_frame["date"] < as_of]
    models = fit_components(history, settings, as_of)

    batch = synthetic_frame[synthetic_frame["date"] >= as_of].head(5).copy()
    batch["home"] = "Yepyeni Takım"
    probs = component_probabilities(models, batch, settings)
    assert np.isnan(probs["dc"]).all()
    # Elo ve form yine de çalışmalı; sistem tek bileşen kaybında durmaz.
    assert not np.isnan(probs["elo"]).any()


def test_walk_forward_respects_end_boundary(settings, synthetic_frame):
    """`end` sonrası hiçbir satır dönmemeli.

    Yeniden kestirim blokları `end`'i taşıyabildiği için bu sınır ayrıca
    uygulanır; taşarsa backtest'in kalibrasyon penceresi değerlendirme
    penceresine sızar ve raporlanan skorlar örnek dışı olmaz.
    """
    end = pd.Timestamp("2020-06-01")
    rows = walk_forward(
        synthetic_frame, settings,
        start=pd.Timestamp("2019-08-01"), end=end, refit_days=45,
    )
    assert not rows.empty
    assert rows["date"].max() <= end


def test_walk_forward_returns_empty_for_out_of_range_window(settings, synthetic_frame):
    result = walk_forward(
        synthetic_frame, settings,
        start=pd.Timestamp("2090-01-01"), end=pd.Timestamp("2090-12-31"),
    )
    assert result.empty


def test_stack_components_shapes(settings, synthetic_frame):
    rows = walk_forward(
        synthetic_frame, settings,
        start=pd.Timestamp("2021-01-01"), end=pd.Timestamp("2021-06-01"), refit_days=60,
    )
    stacks = stack_components(rows)
    assert set(stacks) == {"dc", "elo", "market", "form"}
    for values in stacks.values():
        assert values.shape == (len(rows), 3)


def test_dixon_coles_covers_cross_division_matches(settings, synthetic_db):
    """Aynı ülkenin farklı seviyeleri birlikte kestirilmeli.

    Spor Toto listeleri Süper Lig ile 1. Lig'i sürekli karıştırır. Gol modeli
    lig başına kestirilirse bu maçlarda hiçbir şey söyleyemez; ülke piramidi
    birlikte kestirildiğinde küme düşme/çıkma iki seviyeyi bağlar.
    """
    from sportoto.config import LEAGUES, league_group

    # T1 ve T2 aynı grupta olmalı, farklı ülkeler ayrı.
    assert league_group("T1") == league_group("T2") == "TR"
    assert league_group("E0") == league_group("E1") == "EN"
    assert league_group("T1") != league_group("E0")
    assert LEAGUES["SP2"].group_key == LEAGUES["SP1"].group_key


def test_league_group_falls_back_to_code():
    from sportoto.config import league_group

    assert league_group("ARG") == "ARG"     # tek seviyeli lig kendi grubu
    assert league_group("BİLİNMEYEN") == "BİLİNMEYEN"
    assert league_group(None) is None
