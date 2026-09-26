from __future__ import annotations

import math
from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from llm_eval_gate.stats import (
    METHOD_AGRESTI_MIN,
    METHOD_BOOTSTRAP,
    METHOD_TANGO,
    bootstrap_mean,
    clustered_proportion,
    discordant_masses,
    effective_n,
    holm,
    mcnemar_exact,
    nearest_rank,
    paired_agresti_min,
    paired_interval,
    paired_tango,
    quantile,
    tango_restricted_worse,
    tango_score,
    wilson,
    wilson_interval,
    z_value,
)

Z95 = z_value(0.90)  # one-sided 5%


def diffs(worse: int, better: int, same: int) -> list[float]:
    return [-1.0] * worse + [1.0] * better + [0.0] * same


# --- Wilson and the effective sample size --------------------------------------------


def test_wilson_matches_the_textbook_value() -> None:
    interval = wilson(18, 20)
    assert interval.point == pytest.approx(0.9)
    assert interval.lo == pytest.approx(0.6990, abs=1e-4)
    assert interval.hi == pytest.approx(0.9721, abs=1e-4)


def test_wilson_stays_inside_the_unit_interval() -> None:
    assert wilson(0, 10).lo == 0.0
    assert wilson(10, 10).hi == 1.0
    assert wilson(200, 200).lo == pytest.approx(0.9812, abs=1e-4)
    assert wilson(0, 0) == wilson_interval(0.0, 0)
    with pytest.raises(ValueError, match="within"):
        wilson(11, 10)
    with pytest.raises(ValueError, match="confidence"):
        z_value(1.0)


def test_effective_n_is_the_number_of_cases_for_binary_rates() -> None:
    rates = [1.0] * 70 + [0.0] * 30
    assert effective_n(rates, observations=300) == pytest.approx(100)
    interval, n_eff = clustered_proportion(rates, 300)
    assert n_eff == pytest.approx(100)
    assert interval == wilson(70, 100)


def test_effective_n_grows_with_independent_repeats() -> None:
    # Every case right 2 of 3 times: no spread between cases, so the repeats carry the
    # information and the effective n is bounded by the observations.
    assert effective_n([2 / 3] * 50, observations=150) == pytest.approx(150)
    mixed = [1.0, 2 / 3, 1 / 3, 0.0] * 25
    n_eff = effective_n(mixed, observations=300)
    assert 100 < n_eff < 300


def test_effective_n_is_conservative_at_the_edges() -> None:
    assert effective_n([1.0] * 40, observations=120) == pytest.approx(40)
    assert effective_n([], observations=0) == 0.0
    with pytest.raises(ValueError, match="at least one"):
        clustered_proportion([], 0)


@given(st.lists(st.sampled_from([0.0, 1 / 3, 2 / 3, 1.0]), min_size=1, max_size=60))
def test_effective_n_is_bounded(rates: list[float]) -> None:
    n_eff = effective_n(rates, observations=3 * len(rates))
    assert len(rates) - 1e-9 <= n_eff <= 3 * len(rates) + 1e-9


# --- Tango score interval -------------------------------------------------------------


def test_tango_with_no_discordance_is_the_wilson_bound() -> None:
    # b = c = 0: the lower bound solves z^2 / (n + z^2), the Wilson bound for 0 events.
    interval = paired_tango([0.0] * 100, alpha=0.05)
    expected = Z95**2 / (100 + Z95**2)
    assert interval.delta == 0.0
    assert interval.lo == pytest.approx(-expected, abs=1e-9)
    assert interval.hi == pytest.approx(expected, abs=1e-9)


def test_identical_runs_need_88_cases_to_clear_a_3pp_margin() -> None:
    assert paired_tango([0.0] * 87, alpha=0.05).lo < -0.03
    assert paired_tango([0.0] * 88, alpha=0.05).lo >= -0.03


def test_tango_bounds_solve_the_score_equation() -> None:
    data = diffs(worse=12, better=5, same=183)
    interval = paired_tango(data, alpha=0.05)
    worse, better = discordant_masses(data)
    assert tango_score(worse, better, len(data), interval.lo) == pytest.approx(Z95, abs=1e-6)
    assert tango_score(worse, better, len(data), interval.hi) == pytest.approx(-Z95, abs=1e-6)
    assert tango_score(worse, better, len(data), interval.delta) == pytest.approx(0.0, abs=1e-9)
    assert interval.lo < interval.delta < interval.hi


def test_tango_handles_every_case_on_one_side() -> None:
    assert paired_tango([-1.0] * 10, alpha=0.05).lo == -1.0
    assert paired_tango([1.0] * 10, alpha=0.05).hi == 1.0
    one = paired_tango([1.0], alpha=0.05)
    assert -1.0 < one.lo < 1.0


def test_restricted_mle_maximises_the_constrained_likelihood() -> None:
    worse, better, n, delta0 = 7.0, 3.0, 60, -0.05
    q = tango_restricted_worse(worse, better, n, delta0)

    def loglik(p: float) -> float:
        return (
            worse * math.log(p)
            + better * math.log(p + delta0)
            + (n - worse - better) * math.log(1 - 2 * p - delta0)
        )

    grid = [(-delta0) + step * 1e-4 for step in range(1, 4000)]
    best = max(grid, key=loglik)
    assert q == pytest.approx(best, abs=2e-4)
    with pytest.raises(ValueError, match="delta0"):
        tango_restricted_worse(1, 1, 10, 1.0)


@settings(max_examples=150)
@given(
    st.integers(min_value=1, max_value=300),
    st.integers(min_value=0, max_value=300),
    st.integers(min_value=0, max_value=300),
)
def test_tango_score_decreases_in_delta0(n: int, worse: int, better: int) -> None:
    worse, better = min(worse, n), min(better, n - min(worse, n))
    grid = [-0.99 + 0.02 * k for k in range(100)]
    values = [tango_score(worse, better, n, d) for d in grid]
    assert all(a >= b - 1e-9 for a, b in pairwise(values))


# --- Wald+2 and the bootstrap ---------------------------------------------------------


def test_agresti_min_matches_propcis() -> None:
    # PropCIs::diffpropci.mp(b = 40, c = 20, n = 160, conf.level = 0.95), computed by hand
    # from the published formula.
    interval = paired_agresti_min(diffs(worse=40, better=20, same=100), alpha=0.025)
    assert interval.lo == pytest.approx(-0.216017, abs=1e-5)
    assert interval.hi == pytest.approx(-0.030896, abs=1e-5)
    assert interval.delta == pytest.approx(-20 / 160)


def test_bootstrap_collapses_without_disagreement() -> None:
    # The failure that ruled the bootstrap out: no discordance, no interval.
    boot = bootstrap_mean([0.0] * 30, resamples=500, alpha=0.05, seed=1)
    assert boot.lo == boot.hi == 0.0
    tango = paired_interval([0.0] * 30, alpha=0.05, method=METHOD_TANGO)
    assert tango.lo < -0.05


def test_bootstrap_is_deterministic_and_brackets_the_mean() -> None:
    values = [float(i % 7) for i in range(60)]
    first = bootstrap_mean(values, resamples=400, alpha=0.05, seed="s")
    second = bootstrap_mean(values, resamples=400, alpha=0.05, seed="s")
    assert first == second
    assert first.lo < first.mean < first.hi
    for kwargs in ({"resamples": 0, "alpha": 0.05}, {"resamples": 10, "alpha": 0.5}):
        with pytest.raises(ValueError):
            bootstrap_mean(values, seed=1, **kwargs)
    with pytest.raises(ValueError, match="at least one"):
        bootstrap_mean([], resamples=10, alpha=0.05, seed=1)


def test_paired_interval_dispatches_and_validates() -> None:
    data = diffs(worse=3, better=4, same=50)
    methods = {
        paired_interval(data, alpha=0.05, method=m).method
        for m in (METHOD_TANGO, METHOD_AGRESTI_MIN, METHOD_BOOTSTRAP)
    }
    assert methods == {METHOD_TANGO, METHOD_AGRESTI_MIN, METHOD_BOOTSTRAP}
    with pytest.raises(ValueError, match="unknown paired method"):
        paired_interval(data, alpha=0.05, method="wald")
    with pytest.raises(ValueError, match="alpha"):
        paired_tango(data, alpha=0.6)
    with pytest.raises(ValueError, match="at least one"):
        paired_agresti_min([], alpha=0.05)


def test_fractional_differences_use_discordant_masses() -> None:
    data = [-1 / 3, 2 / 3, 0.0, -1.0]
    assert discordant_masses(data) == (pytest.approx(4 / 3), pytest.approx(2 / 3))
    interval = paired_tango(data * 30, alpha=0.05)
    assert interval.lo < interval.delta < interval.hi


# --- tests and adjustments ------------------------------------------------------------


def test_mcnemar_exact_tail() -> None:
    result = mcnemar_exact(5, 0)
    assert result.p_worse == pytest.approx(1 / 32)
    assert result.p_two_sided == pytest.approx(1 / 16)
    assert mcnemar_exact(0, 0).p_two_sided == 1.0
    assert mcnemar_exact(3, 3).p_two_sided == 1.0
    with pytest.raises(ValueError, match="non-negative"):
        mcnemar_exact(-1, 2)


def test_holm_is_monotone_and_capped() -> None:
    adjusted = holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adjusted["a"] == pytest.approx(0.03)
    assert adjusted["c"] == pytest.approx(0.06)
    assert adjusted["b"] == pytest.approx(0.06)
    assert holm({"x": 0.9, "y": 0.8})["x"] == 1.0
    assert holm({}) == {}


def test_quantiles() -> None:
    assert quantile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert quantile([5.0], 0.9) == 5.0
    assert nearest_rank([10.0, 1.0, 5.0], 0.95) == 10.0
    assert nearest_rank([10.0, 1.0, 5.0], 0.5) == 5.0
    for bad in (
        lambda: quantile([], 0.5),
        lambda: quantile([1.0], 1.5),
        lambda: nearest_rank([], 0.5),
    ):
        with pytest.raises(ValueError):
            bad()
