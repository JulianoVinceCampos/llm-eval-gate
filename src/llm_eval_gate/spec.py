"""Executable spec: requirements with acceptance criteria, plus the regression policy.

A requirement is data, not prose: split, metric, operator, threshold, the evidence level
(point estimate or a bound of the interval) and whether it blocks. The gate evaluates it,
and the traceability report links requirement -> cases -> result, including the
requirements that have no evidence at all. A spec nobody can execute is a wish list.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_eval_gate.domain import EVAL_SPLITS
from llm_eval_gate.jsonio import read_text, sha256_text
from llm_eval_gate.labels import parse_label
from llm_eval_gate.metrics import ALL_METRICS, LABELLED_METRICS, PROPORTION_METRICS
from llm_eval_gate.stats import METHOD_TANGO, PAIRED_METHODS

REQ_ID = re.compile(r"^REQ-\d{3}$")
OPERATORS = (">=", "<=")
EVIDENCE = ("point", "lower", "upper")
ENFORCEMENT = ("block", "report")
INCONCLUSIVE_POLICIES = ("fail", "warn")


class SpecError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Requirement:
    id: str
    title: str
    rationale: str
    split: str
    metric: str
    op: str
    threshold: float
    evidence: str
    enforcement: str
    label: str | None = None

    def criterion(self) -> str:
        target = f"{self.metric}({self.label})" if self.label else self.metric
        bound = {
            "point": "",
            "lower": " [limite inferior IC95]",
            "upper": " [limite superior IC95]",
        }
        return f"{target}[{self.split}] {self.op} {self.threshold:g}{bound[self.evidence]}"


@dataclass(frozen=True, slots=True)
class Spec:
    id: str
    version: str
    title: str
    requirements: tuple[Requirement, ...]
    sha256: str


@dataclass(frozen=True, slots=True)
class RegressionPolicy:
    alpha: float
    margin: float
    resamples: int
    seed: int
    splits: tuple[str, ...]
    on_inconclusive: str
    method: str = METHOD_TANGO


@dataclass(frozen=True, slots=True)
class Policy:
    regression: RegressionPolicy
    require_evidence: bool
    sha256: str


def _load_toml(path: Path) -> tuple[dict[str, Any], str]:
    text = read_text(path)
    try:
        return tomllib.loads(text), sha256_text(text)
    except tomllib.TOMLDecodeError as error:
        raise SpecError([f"{path.name}: invalid TOML: {error}"]) from None


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _float(value: object) -> float:
    """A value `_number` already accepted, as a float."""
    if not isinstance(value, int | float):
        raise TypeError(f"expected a number, got {value!r}")
    return float(value)


def _requirement(raw: Mapping[str, Any], index: int, problems: list[str]) -> Requirement | None:
    where = f"requirement #{index + 1}"
    allowed = {
        "id",
        "title",
        "rationale",
        "split",
        "metric",
        "op",
        "threshold",
        "evidence",
        "enforcement",
        "label",
    }
    unknown = sorted(set(raw) - allowed)
    if unknown:
        problems.append(f"{where}: unknown keys {unknown}")
    rid = raw.get("id")
    if not isinstance(rid, str) or not REQ_ID.match(rid):
        problems.append(f"{where}: id must look like REQ-001")
        return None
    where = rid
    before = len(problems)
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        problems.append(f"{where}: title is required")
    split = raw.get("split")
    if split not in EVAL_SPLITS:
        problems.append(f"{where}: split must be one of {list(EVAL_SPLITS)}")
    metric = raw.get("metric")
    if metric not in ALL_METRICS:
        problems.append(f"{where}: metric must be one of {sorted(ALL_METRICS)}")
    label = raw.get("label")
    if metric in LABELLED_METRICS:
        if not isinstance(label, str) or parse_label(label) is None:
            problems.append(f"{where}: metric {metric} needs a valid `label`")
    elif label is not None:
        problems.append(f"{where}: `label` only applies to {sorted(LABELLED_METRICS)}")
    op = raw.get("op")
    if op not in OPERATORS:
        problems.append(f"{where}: op must be one of {list(OPERATORS)}")
    threshold = raw.get("threshold")
    if not _number(threshold):
        problems.append(f"{where}: threshold must be a number")
    evidence = raw.get("evidence", "point")
    if evidence not in EVIDENCE:
        problems.append(f"{where}: evidence must be one of {list(EVIDENCE)}")
    elif evidence != "point" and metric not in PROPORTION_METRICS:
        problems.append(f"{where}: interval evidence needs a proportion metric, {metric} has none")
    enforcement = raw.get("enforcement", "block")
    if enforcement not in ENFORCEMENT:
        problems.append(f"{where}: enforcement must be one of {list(ENFORCEMENT)}")
    if len(problems) > before:
        return None
    return Requirement(
        id=rid,
        title=str(title),
        rationale=str(raw.get("rationale", "")),
        split=str(split),
        metric=str(metric),
        op=str(op),
        threshold=_float(threshold),
        evidence=str(evidence),
        enforcement=str(enforcement),
        label=str(parse_label(label)) if isinstance(label, str) else None,
    )


def load_spec(path: Path) -> Spec:
    data, digest = _load_toml(path)
    problems: list[str] = []
    meta = data.get("meta")
    if not isinstance(meta, dict):
        raise SpecError([f"{path.name}: missing [meta] table"])
    raw_requirements = data.get("requirement", [])
    if not isinstance(raw_requirements, list) or not raw_requirements:
        raise SpecError([f"{path.name}: at least one [[requirement]] is needed"])
    requirements: list[Requirement] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_requirements):
        if not isinstance(raw, dict):
            problems.append(f"requirement #{index + 1}: must be a table")
            continue
        requirement = _requirement(raw, index, problems)
        if requirement is None:
            continue
        if requirement.id in seen:
            problems.append(f"{requirement.id}: duplicated id")
        seen.add(requirement.id)
        requirements.append(requirement)
    if problems:
        raise SpecError(problems)
    return Spec(
        id=str(meta.get("id", "spec")),
        version=str(meta.get("version", "0")),
        title=str(meta.get("title", "")),
        requirements=tuple(requirements),
        sha256=digest,
    )


def load_policy(path: Path) -> Policy:
    data, digest = _load_toml(path)
    problems: list[str] = []
    regression = data.get("regression")
    coverage = data.get("coverage", {})
    if not isinstance(regression, dict):
        raise SpecError([f"{path.name}: missing [regression] table"])
    alpha = regression.get("alpha")
    margin = regression.get("margin")
    resamples = regression.get("resamples", 2000)
    seed = regression.get("seed", 0)
    splits = regression.get("splits", list(EVAL_SPLITS))
    on_inconclusive = regression.get("on_inconclusive", "fail")
    method = regression.get("method", METHOD_TANGO)
    unknown = sorted(
        set(regression)
        - {"alpha", "margin", "resamples", "seed", "splits", "on_inconclusive", "method"}
    )
    if unknown:
        problems.append(f"regression: unknown keys {unknown}")
    if method not in PAIRED_METHODS:
        problems.append(f"regression.method must be one of {list(PAIRED_METHODS)}")
    if not _number(alpha) or not 0.0 < _float(alpha) < 0.5:
        problems.append("regression.alpha must be in (0, 0.5)")
    if not _number(margin) or not 0.0 <= _float(margin) < 1.0:
        problems.append("regression.margin must be in [0, 1)")
    if not isinstance(resamples, int) or not 100 <= resamples <= 100_000:
        problems.append("regression.resamples must be an integer in [100, 100000]")
    if not isinstance(seed, int):
        problems.append("regression.seed must be an integer")
    if not isinstance(splits, list) or not splits or any(s not in EVAL_SPLITS for s in splits):
        problems.append(f"regression.splits must be a non-empty subset of {list(EVAL_SPLITS)}")
    if on_inconclusive not in INCONCLUSIVE_POLICIES:
        problems.append(f"regression.on_inconclusive must be one of {list(INCONCLUSIVE_POLICIES)}")
    require_evidence = (
        coverage.get("require_evidence", True) if isinstance(coverage, dict) else True
    )
    if not isinstance(require_evidence, bool):
        problems.append("coverage.require_evidence must be a boolean")
    if problems:
        raise SpecError([f"{path.name}: {problem}" for problem in problems])
    return Policy(
        regression=RegressionPolicy(
            alpha=_float(alpha),
            margin=_float(margin),
            resamples=int(resamples),
            seed=int(seed),
            splits=tuple(str(s) for s in splits),
            on_inconclusive=str(on_inconclusive),
            method=str(method),
        ),
        require_evidence=bool(require_evidence),
        sha256=digest,
    )
