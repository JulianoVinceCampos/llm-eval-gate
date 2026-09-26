"""Application services (Facade): the operations the CLI, CI and the dashboard call.

Order of `ci` mirrors the argument of the project: first prove the artifacts are what the
generators produce (datasets, fitted baseline), then evaluate every candidate against its
accepted reference and the spec. If the first step fails nothing else is trustworthy, so
the run stops there.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from llm_eval_gate.baseline.signature import SignatureModel, fit
from llm_eval_gate.calibration import CalibrationResult, CalibrationSpec, run_calibration
from llm_eval_gate.config import CandidateConfig, ConfigError, load_candidates
from llm_eval_gate.datasets.generate import generate_all
from llm_eval_gate.datasets.store import (
    load_eval_cases,
    load_manifest,
    load_split,
    verify,
    write_all,
)
from llm_eval_gate.domain import EVAL_SPLITS, SPLIT_TRAIN, Case, RunRecord
from llm_eval_gate.factory import PendingEvidenceError, ProviderMode, build_classifier
from llm_eval_gate.gate import GateContext, GateEngine, non_inferiority, pair_stats
from llm_eval_gate.jsonio import SchemaError, read_json, read_text, write_json, write_text
from llm_eval_gate.metrics import case_stats, run_metrics
from llm_eval_gate.providers.base import ProviderError
from llm_eval_gate.reference import Reference, accept, load_reference, save_reference
from llm_eval_gate.report import readme_block
from llm_eval_gate.results import (
    STATUS_ERROR,
    STATUS_EVALUATED,
    STATUS_PENDING,
    CandidateResult,
    CIOutcome,
    ClassComparison,
    Comparison,
)
from llm_eval_gate.runner import EvalRunner, RunListener
from llm_eval_gate.scenarios import ScenarioOutcome, evaluate_scenario, load_scenarios
from llm_eval_gate.spec import Policy, Spec, load_policy, load_spec
from llm_eval_gate.stats import holm, mcnemar_exact
from llm_eval_gate.trace import build_trace

README_START = "<!-- llm-eval-gate:results:start -->"
README_END = "<!-- llm-eval-gate:results:end -->"


@dataclass(frozen=True, slots=True)
class Project:
    root: Path

    @property
    def candidates_dir(self) -> Path:
        return self.root / "evals" / "candidates"

    @property
    def scenarios_dir(self) -> Path:
        return self.root / "evals" / "scenarios"

    @property
    def runs_dir(self) -> Path:
        return self.root / "evals" / "runs"

    @property
    def model_path(self) -> Path:
        return self.root / "evals" / "models" / "rules-signature.json"

    @property
    def calibration_path(self) -> Path:
        return self.root / "evals" / "calibration.json"

    @property
    def spec_path(self) -> Path:
        return self.root / "spec" / "requirements.toml"

    @property
    def policy_path(self) -> Path:
        return self.root / "spec" / "policy.toml"

    @property
    def readme_path(self) -> Path:
        return self.root / "README.md"


@dataclass(frozen=True, slots=True)
class Workspace:
    project: Project
    cases: tuple[Case, ...]
    dataset_sha256: str
    spec: Spec
    policy: Policy


def load_workspace(project: Project) -> Workspace:
    manifest = load_manifest(project.root)
    return Workspace(
        project=project,
        cases=tuple(load_eval_cases(project.root)),
        dataset_sha256=str(manifest["eval_sha256"]),
        spec=load_spec(project.spec_path),
        policy=load_policy(project.policy_path),
    )


# --- artifacts ------------------------------------------------------------------------


def build_datasets(project: Project, *, check: bool) -> list[str]:
    if check:
        return verify(project.root)
    write_all(project.root, generate_all())
    return []


def fitted_model(project: Project) -> SignatureModel:
    manifest = load_manifest(project.root)
    train_sha = str(manifest["splits"][SPLIT_TRAIN]["sha256"])
    return fit(load_split(project.root, SPLIT_TRAIN), train_sha256=train_sha)


def fit_baseline(project: Project, *, check: bool) -> list[str]:
    model = fitted_model(project)
    relative = project.model_path.relative_to(project.root).as_posix()
    if check:
        if not project.model_path.exists():
            return [f"{relative}: missing, run `llm-eval-gate fit`"]
        committed = SignatureModel.from_dict(read_json(project.model_path))
        return [] if committed == model else [f"{relative}: out of date, run `llm-eval-gate fit`"]
    write_json(project.model_path, model.to_dict())
    return []


# --- evaluation -----------------------------------------------------------------------


def assess(
    ws: Workspace, config: CandidateConfig, run: RunRecord, reference: Reference | None
) -> CandidateResult:
    metrics = run_metrics(ws.cases, run, EVAL_SPLITS)
    run_stats = case_stats(ws.cases, run)
    reference_stats = case_stats(ws.cases, reference.run) if reference is not None else {}
    ctx = GateContext(
        candidate=config.name,
        stage=config.stage,
        cases=ws.cases,
        run=run,
        reference=reference.run if reference is not None else None,
        metrics=metrics,
        run_stats=run_stats,
        reference_stats=reference_stats if reference is not None else None,
        spec=ws.spec,
        policy=ws.policy,
    )
    report = GateEngine.standard(ws.spec, ws.policy).evaluate(ctx)
    return CandidateResult(
        config=config,
        status=STATUS_EVALUATED,
        run=run,
        reference=reference,
        metrics=metrics,
        report=report,
        trace=build_trace(ws.spec, report.checks, run_stats),
        run_stats=run_stats,
        reference_stats=reference_stats,
    )


def execute(
    ws: Workspace,
    config: CandidateConfig,
    mode: ProviderMode,
    *,
    listener: RunListener | None = None,
) -> RunRecord:
    """Run one candidate. Raises PendingEvidenceError when replay has nothing to replay."""
    built = build_classifier(config, ws.project.root, mode)
    runner = EvalRunner(built.classifier, prices=config.prices, listener=listener)
    try:
        return runner.run(config, ws.cases, ws.dataset_sha256)
    finally:
        # Partial recordings are kept: a live run that dies at call 400 of 600 should not
        # throw away 400 paid answers.
        built.persist()


def evaluate_candidate(
    ws: Workspace,
    config: CandidateConfig,
    mode: ProviderMode = ProviderMode.REPLAY,
    *,
    listener: RunListener | None = None,
) -> CandidateResult:
    reference = load_reference(ws.project.root, config.name)
    try:
        run = execute(ws, config, mode, listener=listener)
    except PendingEvidenceError as error:
        return CandidateResult(config, STATUS_PENDING, reference=reference, message=str(error))
    except ProviderError as error:
        return CandidateResult(config, STATUS_ERROR, reference=reference, message=str(error))
    return assess(ws, config, run, reference)


def candidate(project: Project, name: str) -> CandidateConfig:
    for config in load_candidates(project.candidates_dir):
        if config.name == name:
            return config
    raise ConfigError(f"no candidate named {name!r} in {project.candidates_dir}")


def run_ci(
    project: Project,
    mode: ProviderMode = ProviderMode.REPLAY,
    *,
    listener: RunListener | None = None,
) -> CIOutcome:
    problems = build_datasets(project, check=True)
    if not problems:
        problems += fit_baseline(project, check=True)
    if problems:
        return CIOutcome((), tuple(problems))
    ws = load_workspace(project)
    results = tuple(
        evaluate_candidate(ws, config, mode, listener=listener)
        for config in load_candidates(project.candidates_dir)
    )
    return CIOutcome(results)


def evidence_results(ws: Workspace) -> list[CandidateResult]:
    """Every candidate assessed on its accepted reference: the numbers the README shows."""
    results: list[CandidateResult] = []
    for config in load_candidates(ws.project.candidates_dir):
        reference = load_reference(ws.project.root, config.name)
        if reference is None:
            results.append(CandidateResult(config, STATUS_PENDING, message="sem referência aceita"))
        else:
            results.append(assess(ws, config, reference.run, reference))
    return results


def _source_commit(root: Path) -> str:
    sha = os.environ.get("GITHUB_SHA")
    if sha:
        return sha
    head = root / ".git" / "HEAD"
    if not head.exists():
        return "unknown"
    content = read_text(head).strip()
    if not content.startswith("ref: "):
        return content
    ref_path = root / ".git" / content.removeprefix("ref: ")
    return read_text(ref_path).strip() if ref_path.exists() else "unknown"


def accept_reference(
    project: Project,
    name: str,
    *,
    mode: ProviderMode = ProviderMode.REPLAY,
    run_path: Path | None = None,
    note: str = "",
    now: datetime | None = None,
) -> Path:
    ws = load_workspace(project)
    config = candidate(project, name)
    if run_path is not None:
        run = RunRecord.from_dict(read_json(run_path))
        if run.candidate != name:
            raise ConfigError(f"{run_path.name} belongs to {run.candidate!r}, not {name!r}")
        if run.dataset_sha256 != ws.dataset_sha256:
            raise ConfigError(f"{run_path.name} was produced on another dataset version")
    else:
        run = execute(ws, config, mode)
    reference = accept(
        run, now=now or datetime.now(UTC), source_commit=_source_commit(project.root), note=note
    )
    return save_reference(project.root, reference)


def save_run(project: Project, run: RunRecord) -> Path:
    path = project.runs_dir / f"{run.candidate}.json"
    write_json(path, run.to_dict())
    return path


# --- comparison -----------------------------------------------------------------------


def compare_results(
    ws: Workspace, a: CandidateResult, b: CandidateResult, split: str
) -> Comparison | None:
    pairs = pair_stats(a.run_stats, b.run_stats, split)
    if not pairs:
        return None
    regression = ws.policy.regression
    result = non_inferiority(
        pairs,
        split=split,
        margin=0.0,
        alpha=0.025,
        method=regression.method,
        resamples=regression.resamples,
        seed=f"compare|{a.name}|{b.name}|{split}",
    )
    shared = sorted(
        case_id
        for case_id, stat in b.run_stats.items()
        if stat.split == split and case_id in a.run_stats
    )
    by_label: dict[str, list[str]] = {}
    for case_id in shared:
        by_label.setdefault(b.run_stats[case_id].label.value, []).append(case_id)
    raw: dict[str, tuple[int, float, float, float]] = {}
    for label, ids in sorted(by_label.items()):
        a_ok = [a.run_stats[i].majority_correct for i in ids]
        b_ok = [b.run_stats[i].majority_correct for i in ids]
        worse = sum(1 for x, y in zip(a_ok, b_ok, strict=True) if x and not y)
        better = sum(1 for x, y in zip(a_ok, b_ok, strict=True) if y and not x)
        raw[label] = (
            len(ids),
            sum(a_ok) / len(ids),
            sum(b_ok) / len(ids),
            mcnemar_exact(worse, better).p_two_sided,
        )
    adjusted = holm({label: values[3] for label, values in raw.items()})
    classes = tuple(
        ClassComparison(label, n, a_rec, b_rec, p, adjusted[label])
        for label, (n, a_rec, b_rec, p) in raw.items()
    )
    # Operator slices over the shared cases only: A restricted to what B saw, or the slice
    # would compare two different sets of incidents.
    shared_set = set(shared)
    case_ops = {
        case.id: {op.split(":")[0] for op in case.operators}
        for case in ws.cases
        if case.id in shared_set
    }
    operators: list[tuple[str, int, float, float]] = []
    for op in sorted({op for ops in case_ops.values() for op in ops}):
        ids = [case_id for case_id in shared if op in case_ops[case_id]]
        a_acc = sum(a.run_stats[i].correct_rate for i in ids) / len(ids)
        b_acc = sum(b.run_stats[i].correct_rate for i in ids) / len(ids)
        operators.append((op, len(ids), a_acc, b_acc))
    return Comparison(
        a=a.name,
        b=b.name,
        split=split,
        n_cases=result.n_cases,
        a_accuracy=sum(p.reference_rate for p in pairs) / len(pairs),
        b_accuracy=sum(p.candidate_rate for p in pairs) / len(pairs),
        delta=result.delta,
        lo=result.lo,
        hi=result.hi,
        mcnemar=result.mcnemar,
        classes=classes,
        operators=tuple(operators),
    )


def baseline_comparisons(ws: Workspace, results: Sequence[CandidateResult]) -> list[Comparison]:
    """Every evaluated LLM candidate against the rule baseline, split by split."""
    baseline = next((r for r in results if r.config.kind == "rules" and r.report), None)
    if baseline is None:
        return []
    comparisons: list[Comparison] = []
    for result in results:
        if result is baseline or result.config.kind != "llm" or result.report is None:
            continue
        for split in EVAL_SPLITS:
            comparison = compare_results(ws, baseline, result, split)
            if comparison is not None:
                comparisons.append(comparison)
    return comparisons


# --- calibration, scenarios, readme ---------------------------------------------------


def committed_calibration(project: Project) -> tuple[CalibrationResult | None, str | None]:
    """The committed calibration, or the reason it cannot be used."""
    path = project.calibration_path
    if not path.exists():
        return None, "evals/calibration.json: missing, run `llm-eval-gate calibrate`"
    try:
        return CalibrationResult.from_dict(read_json(path)), None
    except SchemaError as error:
        return None, f"evals/calibration.json: {error}"


def calibrate(
    project: Project,
    *,
    check: bool,
    spec: CalibrationSpec | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[CalibrationResult, list[str]]:
    """Write mode runs `spec` (default: this version's CalibrationSpec). Check mode re-runs
    the spec recorded in the committed file and demands the same numbers, so the check
    proves the published table is what this code computes, not what someone typed."""
    if not check:
        result = run_calibration(spec or CalibrationSpec(), progress)
        write_json(project.calibration_path, result.to_dict())
        return result, []
    committed, problem = committed_calibration(project)
    if committed is None:
        return CalibrationResult(spec or CalibrationSpec(), ()), [problem or "unreadable"]
    result = run_calibration(committed.spec, progress)
    if committed != result:
        return result, ["evals/calibration.json: the statistics changed; re-run and review"]
    return result, []


def run_scenarios(ws: Workspace) -> list[ScenarioOutcome]:
    return [
        evaluate_scenario(scenario, ws.cases, ws.policy.regression)
        for scenario in load_scenarios(ws.project.scenarios_dir)
    ]


def render_readme(project: Project, *, check: bool) -> list[str]:
    ws = load_workspace(project)
    evidence = evidence_results(ws)
    calibration, problem = committed_calibration(project)
    if problem is not None and project.calibration_path.exists():
        # Present but unreadable: rendering without it would silently drop the evidence
        # the README promises. Missing is allowed (a fresh clone before `calibrate`).
        return [problem]
    block = readme_block(
        evidence, baseline_comparisons(ws, evidence), calibration, run_scenarios(ws)
    )
    text = read_text(project.readme_path)
    start = text.find(README_START)
    end = text.find(README_END)
    if start == -1 or end == -1 or end < start:
        return [f"README.md: markers {README_START} / {README_END} not found"]
    updated = text[: start + len(README_START)] + "\n\n" + block + "\n" + text[end:]
    if check:
        return (
            []
            if updated == text
            else ["README.md: results block is stale, run `llm-eval-gate readme`"]
        )
    write_text(project.readme_path, updated)
    return []
