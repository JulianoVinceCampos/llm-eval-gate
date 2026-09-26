"""Statistics the gate relies on. Standard library only, every method named and cited.

- Wilson score interval for proportions (Wilson, 1927). Behaves at p near 0 or 1, where
  the normal approximation produces intervals outside [0, 1].
- Wilson evaluated at the effective sample size (Kish, 1965) for the accuracy of a model
  sampled more than once per case. Repeats of one case are not independent evidence; the
  design effect measured from the case-level variance says how much they are worth.
- Tango score interval for a paired difference of proportions (Tango, 1998), the interval
  the gate decides on (ADR-0002). A score test puts the variance under the null being
  tested, so "no disagreement in 30 cases" still leaves room for a 3 p.p. regression, as
  it should. The pass and fail decisions are the two one-sided score tests at -margin,
  the textbook non-inferiority test for matched pairs.
- Wald+2 interval (Agresti and Min, 2005): half an observation added to each cell of the
  paired table, then Wald. Simple and far better than plain Wald; kept as a comparison arm.
- Percentile bootstrap of a mean, resampling whole cases (Efron, 1979). Also a comparison
  arm: with few discordant cases every resample has the same mean, the interval collapses
  and the gate passes regressions it cannot see. The calibration table measures it.

When a model is sampled R times per case the per-case difference is fractional. The paired
methods then work on the discordant masses b = sum(max(-d, 0)) and c = sum(max(d, 0)); with
R = 1 they are the published formulas, and with R > 1 they treat |d| as if it were d^2,
which overstates the variance and errs on the side of not passing.
- Exact McNemar test for paired binary outcomes (McNemar, 1947), via the binomial tail.
- Holm step-down adjustment for a family of p-values (Holm, 1979).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import NormalDist

from llm_eval_gate.rng import Rng

METHOD_TANGO = "tango"
METHOD_AGRESTI_MIN = "agresti-min"
METHOD_BOOTSTRAP = "bootstrap"
PAIRED_METHODS: tuple[str, ...] = (METHOD_TANGO, METHOD_AGRESTI_MIN, METHOD_BOOTSTRAP)
_BISECTION_STEPS = 64


@dataclass(frozen=True, slots=True)
class Interval:
    point: float
    lo: float
    hi: float


def z_value(confidence: float) -> float:
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    return NormalDist().inv_cdf(0.5 + confidence / 2)


def wilson_interval(p: float, n: float, confidence: float = 0.95) -> Interval:
    """Wilson score interval for a proportion `p` observed over `n` (possibly effective) trials."""
    if n <= 0:
        return Interval(0.0, 0.0, 1.0)
    if not 0.0 <= p <= 1.0:
        raise ValueError("p must be within [0, 1]")
    z = z_value(confidence)
    z2 = z * z
    denominator = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denominator
    # At p = 0 (or 1) the bound is exactly 0 (or 1); rounding would print 2.8e-17 instead.
    lo = 0.0 if p <= 0.0 else max(0.0, center - half)
    hi = 1.0 if p >= 1.0 else min(1.0, center + half)
    return Interval(p, lo, hi)


def wilson(successes: int, n: int, confidence: float = 0.95) -> Interval:
    if n <= 0:
        return Interval(0.0, 0.0, 1.0)
    if not 0 <= successes <= n:
        raise ValueError("successes must be within [0, n]")
    return wilson_interval(successes / n, n, confidence)


def effective_n(rates: Sequence[float], observations: int) -> float:
    """Kish effective sample size of a mean of per-case rates in [0, 1].

    n_eff = p(1 - p) / Var(p_hat), with Var(p_hat) estimated from the spread of the case
    rates. Binary rates (one judgement per case, or repeats that always agree) give exactly
    the number of cases; independent repeats approach the number of observations. The
    result is bounded to [cases, observations]: repeats never count for less than one case
    and never for more than themselves. With p in {0, 1} the spread says nothing about the
    repeats, so the conservative answer, the number of cases, is returned.
    """
    n = len(rates)
    if n == 0:
        return 0.0
    upper = float(max(observations, n))
    p = math.fsum(rates) / n
    bernoulli = p * (1 - p)
    if bernoulli <= 0.0:
        return float(n)
    spread = math.fsum((rate - p) ** 2 for rate in rates) / n
    if spread <= 0.0:
        return upper
    return min(upper, max(float(n), n * bernoulli / spread))


def clustered_proportion(
    rates: Sequence[float], observations: int, confidence: float = 0.95
) -> tuple[Interval, float]:
    """Accuracy of a model sampled once or more per case: Wilson at the effective n."""
    if not rates:
        raise ValueError("clustered_proportion needs at least one case")
    n_eff = effective_n(rates, observations)
    p = math.fsum(rates) / len(rates)
    return wilson_interval(min(1.0, max(0.0, p)), n_eff, confidence), n_eff


@dataclass(frozen=True, slots=True)
class PairedInterval:
    """Interval for the mean paired difference candidate - reference, per case."""

    delta: float
    lo: float
    hi: float
    method: str
    resamples: int = 0


def _check_paired(diffs: Sequence[float], alpha: float) -> None:
    if not diffs:
        raise ValueError("paired interval needs at least one case")
    if not 0.0 < alpha < 0.5:
        raise ValueError("alpha must be in (0, 0.5)")


def discordant_masses(diffs: Sequence[float]) -> tuple[float, float]:
    """(b, c): mass where the candidate is worse, and where it is better."""
    worse = math.fsum(max(-d, 0.0) for d in diffs)
    better = math.fsum(max(d, 0.0) for d in diffs)
    return worse, better


def tango_restricted_worse(worse: float, better: float, n: int, delta0: float) -> float:
    """Maximum likelihood of P(worse) under H0: P(better) - P(worse) = delta0.

    The root of 2n q^2 + a q - b delta0 (1 - delta0) = 0, a = -(b + c) + (2n - c + b) delta0,
    in the parametrisation of PropCIs::scoreci.mp. Clamped to the admissible region (every
    cell non-negative), which only rounding can leave.
    """
    if not -1.0 < delta0 < 1.0:
        raise ValueError("delta0 must be in (-1, 1)")
    a = -(worse + better) + (2 * n - better + worse) * delta0
    discriminant = max(0.0, a * a + 8 * n * worse * delta0 * (1 - delta0))
    q = (math.sqrt(discriminant) - a) / (4 * n)
    return min(max(q, 0.0, -delta0), (1 - delta0) / 2)


def tango_score(worse: float, better: float, n: int, delta0: float) -> float:
    """Tango's score statistic for H0: E[d] = delta0, d = +1 better, -1 worse.

    The variance of d is evaluated under H0, at the restricted estimate of the cells, and
    not where the data landed. That is what keeps the test honest with few discordant cases.
    """
    q = tango_restricted_worse(worse, better, n, delta0)
    variance = 2 * q + delta0 * (1 - delta0)
    numerator = better - worse - n * delta0
    if variance <= 0.0:
        return math.copysign(math.inf, numerator) if numerator else 0.0
    return numerator / math.sqrt(n * variance)


def _tango_bound(worse: float, better: float, n: int, z: float, *, lower: bool) -> float:
    """Invert the score test by bisection. The statistic decreases in delta0."""
    estimate = (better - worse) / n
    edge = -1.0 if lower else 1.0
    if (lower and estimate <= -1.0) or (not lower and estimate >= 1.0):
        return edge
    inside, outside = estimate, edge
    for _ in range(_BISECTION_STEPS):
        mid = (inside + outside) / 2
        if mid in (inside, outside):
            break
        stat = tango_score(worse, better, n, mid)
        rejected = stat >= z if lower else stat <= -z
        if rejected:
            outside = mid
        else:
            inside = mid
    # `outside` is on the rejected side: the bound never claims more than the test does.
    return outside


def paired_tango(diffs: Sequence[float], *, alpha: float) -> PairedInterval:
    """Score interval (Tango, 1998) with each bound at one-sided level `alpha`."""
    _check_paired(diffs, alpha)
    n = len(diffs)
    worse, better = discordant_masses(diffs)
    z = NormalDist().inv_cdf(1 - alpha)
    return PairedInterval(
        delta=math.fsum(diffs) / n,
        lo=_tango_bound(worse, better, n, z, lower=True),
        hi=_tango_bound(worse, better, n, z, lower=False),
        method=METHOD_TANGO,
    )


def paired_agresti_min(diffs: Sequence[float], *, alpha: float) -> PairedInterval:
    """Wald+2 interval (Agresti and Min, 2005), bounds at one-sided level `alpha` each.

    With binary outcomes, b = cases only the reference got right and c = the opposite:
    estimate (c - b) / (n + 2), sd sqrt((b + c + 1) - (c - b)^2 / (n + 2)) / (n + 2),
    truncated to [-1, 1] (same formula as PropCIs::diffpropci.mp).
    """
    _check_paired(diffs, alpha)
    n = len(diffs)
    worse, better = discordant_masses(diffs)
    adjusted = n + 2
    center = (better - worse) / adjusted
    variance = max(0.0, (worse + better + 1) - (better - worse) ** 2 / adjusted)
    sd = math.sqrt(variance) / adjusted
    z = NormalDist().inv_cdf(1 - alpha)
    return PairedInterval(
        delta=math.fsum(diffs) / n,
        lo=max(-1.0, center - z * sd),
        hi=min(1.0, center + z * sd),
        method=METHOD_AGRESTI_MIN,
    )


def paired_interval(
    diffs: Sequence[float],
    *,
    alpha: float,
    method: str = METHOD_TANGO,
    resamples: int = 2000,
    seed: int | str = 0,
) -> PairedInterval:
    if method == METHOD_TANGO:
        return paired_tango(diffs, alpha=alpha)
    if method == METHOD_AGRESTI_MIN:
        return paired_agresti_min(diffs, alpha=alpha)
    if method == METHOD_BOOTSTRAP:
        boot = bootstrap_mean(diffs, resamples=resamples, alpha=alpha, seed=seed)
        return PairedInterval(boot.mean, boot.lo, boot.hi, METHOD_BOOTSTRAP, resamples)
    raise ValueError(f"unknown paired method {method!r}; known: {list(PAIRED_METHODS)}")


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear interpolation between closest ranks (Hyndman and Fan, type 7)."""
    if not sorted_values:
        raise ValueError("quantile of an empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    position = (len(sorted_values) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return float(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * fraction)


def nearest_rank(values: Sequence[float], q: float) -> float:
    """Percentile by nearest rank. Always an observed value, which is what a latency SLO is."""
    if not values:
        raise ValueError("percentile of an empty sequence")
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return float(ordered[rank - 1])


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    mean: float
    lo: float
    hi: float
    resamples: int
    alpha: float


def bootstrap_mean(
    values: Sequence[float], *, resamples: int, alpha: float, seed: int | str
) -> BootstrapResult:
    """Percentile interval [q(alpha), q(1 - alpha)] of the mean. Two-sided level 1 - 2*alpha."""
    n = len(values)
    if n == 0:
        raise ValueError("bootstrap needs at least one value")
    if resamples < 1:
        raise ValueError("resamples must be positive")
    if not 0.0 < alpha < 0.5:
        raise ValueError("alpha must be in (0, 0.5)")
    data = [float(value) for value in values]
    mean = math.fsum(data) / n
    if all(value == data[0] for value in data):
        # Every resample has the same mean. Skipping the loop is exact, not an approximation.
        return BootstrapResult(mean, mean, mean, resamples, alpha)
    draw = Rng("bootstrap", seed, n).stream()
    means: list[float] = []
    for _ in range(resamples):
        total = 0.0
        for _ in range(n):
            total += data[int(draw() * n)]
        means.append(total / n)
    means.sort()
    return BootstrapResult(
        mean, quantile(means, alpha), quantile(means, 1 - alpha), resamples, alpha
    )


@dataclass(frozen=True, slots=True)
class McNemar:
    worse: int
    better: int
    p_worse: float
    p_two_sided: float


def mcnemar_exact(worse: int, better: int) -> McNemar:
    """Paired test on discordant cases.

    `worse`: the reference got it right and the candidate did not. `better`: the opposite.
    Under the null of no difference each discordant case is a fair coin, so the evidence
    that the candidate is worse is the binomial tail P(X >= worse), X ~ Bin(n, 1/2).
    """
    if worse < 0 or better < 0:
        raise ValueError("counts must be non-negative")
    n = worse + better
    if n == 0:
        return McNemar(worse, better, 1.0, 1.0)
    total = 2**n
    upper = sum(math.comb(n, k) for k in range(worse, n + 1))
    lower = sum(math.comb(n, k) for k in range(worse + 1))
    return McNemar(worse, better, upper / total, min(1.0, 2 * min(upper, lower) / total))


def holm(pvalues: Mapping[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values, monotone and capped at 1."""
    ordered = sorted(pvalues.items(), key=lambda item: (item[1], item[0]))
    m = len(ordered)
    adjusted: dict[str, float] = {}
    running = 0.0
    for index, (key, p) in enumerate(ordered):
        running = max(running, min(1.0, (m - index) * p))
        adjusted[key] = running
    return adjusted
