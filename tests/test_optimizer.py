"""Kupon optimizasyonu — kritik bileşen, kaba kuvvetle karşılaştırılır."""

import itertools
import math
import random

import pytest

from sportoto.coupon.optimizer import (
    correct_count_distribution,
    budget_frontier,
    optimize_coupon,
)
from sportoto.coupon.pricing import columns_for, largest_feasible

RNG = random.Random(2024)


def random_probs():
    values = [RNG.random() ** 1.6 + 0.05 for _ in range(3)]
    total = sum(values)
    return tuple(v / total for v in values)


def brute_force(probs_list, max_columns):
    """Tüm 3^n atamayı deneyerek kesin optimumu bulur (yalnızca test için)."""
    best = 0.0
    for sizes in itertools.product((1, 2, 3), repeat=len(probs_list)):
        columns = math.prod(sizes)
        if columns > max_columns:
            continue
        p = 1.0
        for probs, size in zip(probs_list, sizes):
            p *= sum(sorted(probs, reverse=True)[:size])
        best = max(best, p)
    return best


@pytest.mark.parametrize("n", [4, 6, 8])
@pytest.mark.parametrize("budget", [1, 3, 8, 24, 96, 500])
def test_dp_matches_brute_force(n, budget):
    """DP küresel optimumu bulmalı — sezgisel bir yaklaşım bunu garanti edemez."""
    for _ in range(4):
        probs = [random_probs() for _ in range(n)]
        plan = optimize_coupon(probs, max_columns=budget)
        assert plan.p_all_correct == pytest.approx(brute_force(probs, budget), abs=1e-12)


@pytest.mark.parametrize("budget", [1, 2, 5, 23, 100, 1000, 10_000])
def test_budget_is_never_exceeded(budget):
    probs = [random_probs() for _ in range(15)]
    plan = optimize_coupon(probs, max_columns=budget)
    assert plan.columns <= budget
    assert plan.columns == columns_for(plan.doubles, plan.triples)
    assert plan.singles + plan.doubles + plan.triples == 15


def test_probability_is_monotone_in_budget():
    """Daha fazla kolon hiçbir zaman daha düşük P(15/15) vermemeli."""
    probs = [random_probs() for _ in range(15)]
    previous = -1.0
    for columns in [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 4096]:
        current = optimize_coupon(probs, max_columns=columns).p_all_correct
        assert current >= previous - 1e-12
        previous = current


def test_distribution_is_consistent_with_plan():
    probs = [random_probs() for _ in range(15)]
    plan = optimize_coupon(probs, max_columns=192)
    assert sum(plan.distribution.values()) == pytest.approx(1.0, abs=1e-10)
    assert plan.distribution[15] == pytest.approx(plan.p_all_correct, abs=1e-12)
    assert plan.probability_at_least(13) >= plan.probability_at_least(14)


def test_single_column_picks_all_favourites():
    probs = [random_probs() for _ in range(15)]
    plan = optimize_coupon(probs, max_columns=1)
    assert plan.columns == 1
    for selection in plan.selections:
        assert selection.size == 1
        best = max(range(3), key=lambda i: selection.probabilities[i])
        assert selection.picks == (("1", "0", "2")[best],)


def test_certain_match_never_gets_extra_picks():
    """Sonucu kesin olan maça kolon harcamak israftır; optimizer bunu yapmamalı."""
    probs = [(0.999, 0.0005, 0.0005)] + [random_probs() for _ in range(14)]
    plan = optimize_coupon(probs, max_columns=64)
    assert plan.selections[0].size == 1


def test_budget_uses_price_when_columns_absent():
    probs = [random_probs() for _ in range(15)]
    plan = optimize_coupon(probs, budget=500.0, column_price=5.0)
    assert plan.columns <= 100
    assert plan.cost <= 500.0


def test_max_triples_constraint_is_respected():
    probs = [random_probs() for _ in range(15)]
    plan = optimize_coupon(probs, max_columns=100_000, max_triples=2)
    assert plan.triples <= 2


def test_frontier_is_sorted_and_monotone():
    probs = [random_probs() for _ in range(15)]
    plans = budget_frontier(probs, column_price=5.0, max_columns=2000)
    columns = [p.columns for p in plans]
    assert columns == sorted(columns)
    assert len(set(columns)) == len(columns)
    probabilities = [p.p_all_correct for p in plans]
    assert all(b >= a - 1e-12 for a, b in zip(probabilities, probabilities[1:]))


def test_correct_count_distribution_edge_cases():
    assert correct_count_distribution([1.0, 1.0, 1.0])[3] == pytest.approx(1.0)
    assert correct_count_distribution([0.0, 0.0])[0] == pytest.approx(1.0)
    dist = correct_count_distribution([0.5, 0.5])
    assert dist[1] == pytest.approx(0.5)


def test_accepts_dicts_and_objects():
    from sportoto.predictor import MatchPrediction

    as_dicts = [{"home": "A", "away": "B", "p_home": 0.5, "p_draw": 0.3, "p_away": 0.2}] * 3
    as_objects = [MatchPrediction("A", "B", "T1", 0.5, 0.3, 0.2)] * 3
    assert optimize_coupon(as_dicts, max_columns=4).p_all_correct == pytest.approx(
        optimize_coupon(as_objects, max_columns=4).p_all_correct
    )


def test_empty_input_raises():
    with pytest.raises(ValueError):
        optimize_coupon([], max_columns=10)


# --- fiyatlandırma ---
def test_guide_example_three_doubles_one_triple_is_24_columns():
    """Spor Toto rehberindeki örnek: 3 ikili + 1 üçlü = 24 kolon."""
    assert columns_for(3, 1) == 24


@pytest.mark.parametrize("limit", [1, 10, 24, 100, 1000, 5000, 100_000])
def test_largest_feasible_never_exceeds_limit(limit):
    columns, doubles, triples = largest_feasible(limit)
    assert columns <= limit
    assert columns == columns_for(doubles, triples)


def test_columns_for_rejects_impossible_input():
    with pytest.raises(ValueError):
        columns_for(-1, 0)
    with pytest.raises(ValueError):
        columns_for(10, 10)


# --- hedef eşiği (Spor Toto 12'den itibaren öder) ---
def brute_force_threshold(probs_list, max_columns, goal):
    """Tüm atamaları deneyerek en iyi P(≥goal) değerini bulur (yalnızca test)."""
    from sportoto.coupon.optimizer import _threshold_probability

    best = 0.0
    for sizes in itertools.product((1, 2, 3), repeat=len(probs_list)):
        if math.prod(sizes) > max_columns:
            continue
        covers = [
            sum(sorted(probs, reverse=True)[:size])
            for probs, size in zip(probs_list, sizes)
        ]
        best = max(best, _threshold_probability(covers, goal))
    return best


@pytest.mark.parametrize("n,goal", [(5, 3), (6, 4), (7, 5), (7, 6)])
@pytest.mark.parametrize("budget", [2, 12, 36, 144])
def test_threshold_search_is_near_optimal(n, goal, budget):
    """P(≥k) çarpanlarına ayrılamadığı için arama sezgiseldir; optimuma çok yakın olmalı."""
    for _ in range(3):
        probs = [random_probs() for _ in range(n)]
        plan = optimize_coupon(probs, max_columns=budget, target=goal)
        exact = brute_force_threshold(probs, budget, goal)
        assert plan.p_target <= exact + 1e-9          # optimumu aşamaz
        assert plan.p_target >= exact * 0.99          # ondan belirgin geri kalamaz
        assert plan.columns <= budget


def test_threshold_optimum_is_never_worse_than_all_correct_solution():
    """Hedefe göre optimize etmek, hepsi-doğru çözümünden kötü olamaz."""
    for _ in range(8):
        probs = [random_probs() for _ in range(15)]
        for goal in (12, 13, 14):
            all_correct = optimize_coupon(probs, max_columns=576, target=15)
            targeted = optimize_coupon(probs, max_columns=576, target=goal)
            assert targeted.probability_at_least(goal) >= (
                all_correct.probability_at_least(goal) - 1e-12
            )


def test_default_target_is_all_correct_and_stays_exact():
    probs = [random_probs() for _ in range(8)]
    plan = optimize_coupon(probs, max_columns=96)
    assert plan.target == 8
    assert plan.p_target == pytest.approx(plan.p_all_correct)
    assert plan.p_all_correct == pytest.approx(brute_force(probs, 96), abs=1e-12)


def test_target_is_clamped_to_valid_range():
    probs = [random_probs() for _ in range(15)]
    assert optimize_coupon(probs, max_columns=24, target=99).target == 15
    assert optimize_coupon(probs, max_columns=24, target=0).target == 1


def test_maximal_allocations_prunes_dominated_combinations():
    from sportoto.coupon.optimizer import _maximal_allocations

    allocations = _maximal_allocations(15, 15, 96)
    assert allocations
    for doubles, triples in allocations:
        assert columns_for(doubles, triples) <= 96
        # Bütçeye sığan ve bunu her boyutta kapsayan başka bir bileşim olmamalı.
        assert columns_for(doubles + 1, triples) > 96
        assert columns_for(doubles, triples + 1) > 96
