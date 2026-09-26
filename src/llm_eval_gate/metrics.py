"""Metrics per split, computed from a run and the ground truth.

Two levels, on purpose:

- observation level: accuracy as the mean over every classification, with a Wilson
  interval at the effective sample size (Kish). This is what "how often is the model
  right" means when it is sampled more than once per input, without pretending that three
  answers to the same incident are three independent incidents.
- case level: the majority answer per case, for the confusion matrix, precision, recall
  and F1. A per-class table built from repeated observations would count each case R
  times and report more certainty than the data holds.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from llm_eval_gate.domain import Case, RunRecord
from llm_eval_gate.labels import INVALID, Label, prediction_values
from llm_eval_gate.stats import clustered_proportion, nearest_rank, wilson

_ORDER = {value: index for index, value in enumerate(prediction_values())}


@dataclass(frozen=True, slots=True)
class CaseStat:
    case_id: str
    split: str
    label: Label
    predictions: tuple[str, ...]
    correct_rate: float
    majority: str
    unstable: bool
    invalid_rate: float

    @property
    def majority_correct(self) -> bool:
        return self.majority == self.label.value


def majority(predictions: Sequence[str]) -> str:
    """Most frequent answer; ties broken by label declaration order, then `invalid`."""
    counts = Counter(predictions)
    return max(counts, key=lambda value: (counts[value], -_ORDER.get(value, len(_ORDER))))


def case_stats(cases: Sequence[Case], run: RunRecord) -> dict[str, CaseStat]:
    by_case = run.by_case()
    stats: dict[str, CaseStat] = {}
    for case in cases:
        observations = by_case.get(case.id)
        if not observations:
            continue
        predictions = tuple(obs.predicted for obs in observations)
        correct = sum(1 for value in predictions if value == case.label.value)
        stats[case.id] = CaseStat(
            case_id=case.id,
            split=case.split,
            label=case.label,
            predictions=predictions,
            correct_rate=correct / len(predictions),
            majority=majority(predictions),
            unstable=len(set(predictions)) > 1,
            invalid_rate=sum(1 for value in predictions if value == INVALID) / len(predictions),
        )
    return stats


@dataclass(frozen=True, slots=True)
class ClassMetrics:
    label: str
    support: int
    predicted: int
    true_positive: int
    precision: float
    recall: float
    f1: float
    recall_lo: float
    recall_hi: float


@dataclass(frozen=True, slots=True)
class SplitMetrics:
    split: str
    n_cases: int
    n_observations: int
    accuracy: float
    accuracy_lo: float
    accuracy_hi: float
    effective_n: float
    majority_accuracy: float
    macro_f1: float
    format_valid_rate: float
    invalid_observations: int
    instability_rate: float
    unstable_cases: int
    latency_p50_ms: float
    latency_p95_ms: float
    cost_per_call_usd: float
    tokens_per_call: float
    per_class: tuple[ClassMetrics, ...]
    confusion: Mapping[str, Mapping[str, int]]
    by_operator: Mapping[str, tuple[int, float]]

    def class_metrics(self, label: str) -> ClassMetrics | None:
        return next((item for item in self.per_class if item.label == label), None)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["per_class"] = [asdict(item) for item in self.per_class]
        data["confusion"] = {true: dict(row) for true, row in self.confusion.items()}
        data["by_operator"] = {
            op: {"cases": n, "accuracy": acc} for op, (n, acc) in self.by_operator.items()
        }
        return data


def split_metrics(cases: Sequence[Case], run: RunRecord, split: str) -> SplitMetrics | None:
    in_split = [case for case in cases if case.split == split]
    stats = case_stats(in_split, run)
    if not stats:
        return None
    rows = [stats[case.id] for case in in_split if case.id in stats]
    observations = [obs for row in rows for obs in run.by_case()[row.case_id]]
    rates = [row.correct_rate for row in rows]
    accuracy, n_eff = clustered_proportion(rates, len(observations))

    labels_present = [label for label in Label if any(row.label is label for row in rows)]
    per_class: list[ClassMetrics] = []
    for label in labels_present:
        support = sum(1 for row in rows if row.label is label)
        predicted = sum(1 for row in rows if row.majority == label.value)
        tp = sum(1 for row in rows if row.label is label and row.majority_correct)
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        interval = wilson(tp, support)
        per_class.append(
            ClassMetrics(
                label.value, support, predicted, tp, precision, recall, f1, interval.lo, interval.hi
            )
        )

    confusion: dict[str, dict[str, int]] = {label.value: {} for label in labels_present}
    for row in rows:
        cell = confusion[row.label.value]
        cell[row.majority] = cell.get(row.majority, 0) + 1

    operators: dict[str, list[float]] = {}
    by_id = {case.id: case for case in in_split}
    for row in rows:
        for operator in sorted({op.split(":")[0] for op in by_id[row.case_id].operators}):
            operators.setdefault(operator, []).append(row.correct_rate)

    invalid = sum(1 for obs in observations if not obs.format_ok)
    unstable = sum(1 for row in rows if row.unstable)
    latencies = [obs.latency_ms for obs in observations]
    return SplitMetrics(
        split=split,
        n_cases=len(rows),
        n_observations=len(observations),
        accuracy=accuracy.point,
        accuracy_lo=accuracy.lo,
        accuracy_hi=accuracy.hi,
        effective_n=n_eff,
        majority_accuracy=sum(1 for row in rows if row.majority_correct) / len(rows),
        macro_f1=sum(item.f1 for item in per_class) / len(per_class) if per_class else 0.0,
        format_valid_rate=1 - invalid / len(observations),
        invalid_observations=invalid,
        instability_rate=unstable / len(rows),
        unstable_cases=unstable,
        latency_p50_ms=nearest_rank(latencies, 0.50),
        latency_p95_ms=nearest_rank(latencies, 0.95),
        cost_per_call_usd=sum(obs.cost_usd for obs in observations) / len(observations),
        tokens_per_call=sum(obs.tokens_in + obs.tokens_out for obs in observations)
        / len(observations),
        per_class=tuple(per_class),
        confusion=confusion,
        by_operator={op: (len(v), sum(v) / len(v)) for op, v in sorted(operators.items())},
    )


def run_metrics(
    cases: Sequence[Case], run: RunRecord, splits: Sequence[str]
) -> dict[str, SplitMetrics]:
    result: dict[str, SplitMetrics] = {}
    for split in splits:
        metrics = split_metrics(cases, run, split)
        if metrics is not None:
            result[split] = metrics
    return result


@dataclass(frozen=True, slots=True)
class MetricValue:
    value: float
    lo: float | None
    hi: float | None
    n: int


PROPORTION_METRICS = frozenset(
    {
        "accuracy",
        "majority_accuracy",
        "recall",
        "precision",
        "format_valid_rate",
        "instability_rate",
    }
)
POINT_METRICS = frozenset({"macro_f1", "latency_p95_ms", "cost_per_call_usd"})
LABELLED_METRICS = frozenset({"recall", "precision"})
ALL_METRICS = PROPORTION_METRICS | POINT_METRICS


def metric_value(metrics: SplitMetrics, name: str, label: str | None = None) -> MetricValue:
    """One named metric with its interval, when the metric has one."""
    if name == "accuracy":
        return MetricValue(
            metrics.accuracy, metrics.accuracy_lo, metrics.accuracy_hi, metrics.n_cases
        )
    if name == "majority_accuracy":
        hits = round(metrics.majority_accuracy * metrics.n_cases)
        interval = wilson(hits, metrics.n_cases)
        return MetricValue(metrics.majority_accuracy, interval.lo, interval.hi, metrics.n_cases)
    if name in LABELLED_METRICS:
        item = metrics.class_metrics(label or "")
        if item is None:
            return MetricValue(0.0, None, None, 0)
        if name == "recall":
            return MetricValue(item.recall, item.recall_lo, item.recall_hi, item.support)
        interval = wilson(item.true_positive, item.predicted)
        return MetricValue(item.precision, interval.lo, interval.hi, item.predicted)
    if name == "format_valid_rate":
        valid = metrics.n_observations - metrics.invalid_observations
        interval = wilson(valid, metrics.n_observations)
        return MetricValue(
            metrics.format_valid_rate, interval.lo, interval.hi, metrics.n_observations
        )
    if name == "instability_rate":
        interval = wilson(metrics.unstable_cases, metrics.n_cases)
        return MetricValue(metrics.instability_rate, interval.lo, interval.hi, metrics.n_cases)
    if name == "macro_f1":
        return MetricValue(metrics.macro_f1, None, None, metrics.n_cases)
    if name == "latency_p95_ms":
        return MetricValue(metrics.latency_p95_ms, None, None, metrics.n_observations)
    if name == "cost_per_call_usd":
        return MetricValue(metrics.cost_per_call_usd, None, None, metrics.n_observations)
    raise KeyError(f"unknown metric {name!r}")
