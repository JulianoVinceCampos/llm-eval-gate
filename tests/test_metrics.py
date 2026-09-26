from __future__ import annotations

import pytest

from llm_eval_gate.domain import SPLIT_HARD, SPLIT_IN, Case, Observation, RunRecord
from llm_eval_gate.labels import INVALID, Label
from llm_eval_gate.metrics import (
    ALL_METRICS,
    case_stats,
    majority,
    metric_value,
    run_metrics,
    split_metrics,
)

CASES = (
    Case("a", SPLIT_HARD, Label.CERT, "en", "x", ("typo", "distractor:acl")),
    Case("b", SPLIT_HARD, Label.CERT, "en", "x", ("noise",)),
    Case("c", SPLIT_HARD, Label.ACL, "pt", "x", ("typo",)),
    Case("d", SPLIT_HARD, Label.NONE, "pt", "x"),
    Case("e", SPLIT_IN, Label.CERT, "en", "x"),
)


def _run(answers: dict[str, list[str]], latency: float = 100.0) -> RunRecord:
    observations = tuple(
        Observation(case_id, repeat, value, latency + repeat, 300, 20, 0.00004)
        for case_id, values in answers.items()
        for repeat, value in enumerate(values)
    )
    return RunRecord("m", "experimental", {"kind": "test"}, "sha", 3, observations)


RUN = _run(
    {
        "a": ["cert", "cert", "cert"],
        "b": ["cert", "acl", INVALID],
        "c": ["cert", "cert", "acl"],
        "d": ["none", "none", "none"],
    }
)


def test_majority_breaks_ties_by_declaration_order() -> None:
    assert majority(["acl", "cert"]) == "cert"
    assert majority([INVALID, "none"]) == "none"
    assert majority(["acl", "acl", "cert"]) == "acl"


def test_case_stats() -> None:
    stats = case_stats(CASES, RUN)
    assert set(stats) == {"a", "b", "c", "d"}
    assert stats["b"].correct_rate == pytest.approx(1 / 3)
    assert stats["b"].unstable
    assert stats["b"].invalid_rate == pytest.approx(1 / 3)
    assert stats["c"].majority == "cert"
    assert not stats["c"].majority_correct
    assert not stats["a"].unstable


def test_split_metrics() -> None:
    m = split_metrics(CASES, RUN, SPLIT_HARD)
    assert m is not None
    assert m.n_cases == 4
    assert m.n_observations == 12
    assert m.accuracy == pytest.approx((1 + 1 / 3 + 1 / 3 + 1) / 4)
    assert m.accuracy_lo < m.accuracy < m.accuracy_hi
    assert 4 <= m.effective_n <= 12
    assert m.majority_accuracy == pytest.approx(0.75)
    assert m.invalid_observations == 1
    assert m.format_valid_rate == pytest.approx(11 / 12)
    assert m.unstable_cases == 2
    assert m.instability_rate == pytest.approx(0.5)
    assert m.latency_p95_ms == pytest.approx(102.0)
    assert m.cost_per_call_usd == pytest.approx(0.00004)
    assert m.tokens_per_call == pytest.approx(320)
    assert m.confusion["acl"] == {"cert": 1}
    cert = m.class_metrics("cert")
    assert cert is not None
    assert (cert.support, cert.predicted, cert.true_positive) == (2, 3, 2)
    assert cert.recall == 1.0
    assert cert.precision == pytest.approx(2 / 3)
    assert m.class_metrics("heap-oom") is None
    assert m.by_operator["typo"] == (2, pytest.approx((1 + 1 / 3) / 2))
    assert m.by_operator["distractor"][0] == 1
    assert m.to_dict()["by_operator"]["noise"]["cases"] == 1


def test_splits_without_observations_are_absent() -> None:
    assert split_metrics(CASES, RUN, SPLIT_IN) is None
    assert set(run_metrics(CASES, RUN, [SPLIT_IN, SPLIT_HARD])) == {SPLIT_HARD}


def test_metric_values_carry_their_intervals() -> None:
    m = split_metrics(CASES, RUN, SPLIT_HARD)
    assert m is not None
    for name in sorted(ALL_METRICS):
        value = metric_value(m, name, "cert")
        assert value.n >= 1, name
        if value.lo is not None and value.hi is not None:
            assert value.lo <= value.value <= value.hi, name
    assert metric_value(m, "recall", "heap-oom").n == 0
    assert metric_value(m, "precision", "cert").n == 3
    assert metric_value(m, "latency_p95_ms").lo is None
    with pytest.raises(KeyError, match="unknown metric"):
        metric_value(m, "vibes")
