from __future__ import annotations

from dataclasses import replace

import pytest

from helpers import synthetic_config, synthetic_run
from llm_eval_gate.domain import (
    SPLIT_HARD,
    SPLIT_IN,
    STAGE_EXPERIMENTAL,
    STAGE_PRODUCTION,
    RunRecord,
)
from llm_eval_gate.gate import (
    CompatibilityCheck,
    GateContext,
    GateEngine,
    NonInferiorityCheck,
    Paired,
    RequirementCheck,
    Verdict,
    decide,
    non_inferiority,
    pair_stats,
    worst,
)
from llm_eval_gate.metrics import case_stats, run_metrics
from llm_eval_gate.pipeline import Workspace, assess
from llm_eval_gate.reference import Reference
from llm_eval_gate.spec import Policy, Requirement, Spec
from llm_eval_gate.stats import METHOD_AGRESTI_MIN, METHOD_BOOTSTRAP, METHOD_TANGO

GOOD = {"in-dist": 1.0, "hard": 0.85}


def _reference(run: RunRecord) -> Reference:
    return Reference(run, "2026-09-26T00:00:00+00:00", "abc", "test")


def _context(
    ws: Workspace,
    run: RunRecord,
    reference: RunRecord | None,
    *,
    stage: str,
    spec: Spec | None = None,
    policy: Policy | None = None,
) -> GateContext:
    return GateContext(
        candidate=run.candidate,
        stage=stage,
        cases=ws.cases,
        run=run,
        reference=reference,
        metrics=run_metrics(ws.cases, run, (SPLIT_IN, SPLIT_HARD)),
        run_stats=case_stats(ws.cases, run),
        reference_stats=case_stats(ws.cases, reference) if reference is not None else None,
        spec=spec or ws.spec,
        policy=policy or ws.policy,
    )


def test_decide_is_three_valued_with_closed_pass_boundary() -> None:
    assert decide(-0.03, 0.01, 0.03) is Verdict.PASS
    assert decide(-0.2, -0.031, 0.03) is Verdict.FAIL
    assert decide(-0.2, -0.03, 0.03) is Verdict.INCONCLUSIVE
    assert decide(-0.05, 0.02, 0.03) is Verdict.INCONCLUSIVE


def test_worst_orders_fail_over_inconclusive_over_pass() -> None:
    assert worst([Verdict.PASS, Verdict.INCONCLUSIVE]) is Verdict.INCONCLUSIVE
    assert worst([Verdict.INCONCLUSIVE, Verdict.FAIL, Verdict.PASS]) is Verdict.FAIL
    assert worst([]) is Verdict.PASS


def test_non_inferiority_records_the_method() -> None:
    pairs = [Paired(1.0, 1.0, True, True)] * 150 + [Paired(1.0, 0.0, True, False)] * 3
    for method in (METHOD_TANGO, METHOD_AGRESTI_MIN, METHOD_BOOTSTRAP):
        result = non_inferiority(
            pairs, split="hard", margin=0.03, alpha=0.05, method=method, resamples=300, seed=1
        )
        assert result.method == method
        assert result.n_cases == 153
        assert result.mcnemar.worse == 3
        assert result.to_dict()["verdict"] == result.verdict.value
    with pytest.raises(ValueError, match="at least one"):
        non_inferiority([], split="hard", margin=0.03, alpha=0.05)


def test_pair_stats_uses_shared_cases_of_one_split(workspace: Workspace) -> None:
    full = synthetic_run(workspace.cases, synthetic_config(), "x")
    half = synthetic_run(workspace.cases, synthetic_config(fraction=0.5), "x")
    pairs = pair_stats(
        case_stats(workspace.cases, full), case_stats(workspace.cases, half), SPLIT_HARD
    )
    assert len(pairs) == 150


def test_identical_run_passes_every_regression_check(workspace: Workspace) -> None:
    run = synthetic_run(
        workspace.cases,
        synthetic_config(accuracy=GOOD, stage=STAGE_PRODUCTION),
        workspace.dataset_sha256,
    )
    result = assess(
        workspace, synthetic_config(accuracy=GOOD, stage=STAGE_PRODUCTION), run, _reference(run)
    )
    assert result.report is not None
    assert result.report.verdict is Verdict.PASS
    assert not result.blocking
    ni = [c for c in result.report.checks if c.kind == "non-inferiority"]
    assert [c.verdict for c in ni] == [Verdict.PASS, Verdict.PASS]
    assert "IC90" in ni[0].summary


def test_real_regression_fails_and_blocks(workspace: Workspace) -> None:
    reference = synthetic_run(
        workspace.cases, synthetic_config(accuracy=GOOD), workspace.dataset_sha256
    )
    worse = synthetic_run(
        workspace.cases,
        synthetic_config(accuracy={"in-dist": 1.0, "hard": 0.7}),
        workspace.dataset_sha256,
    )
    result = assess(workspace, synthetic_config(), worse, _reference(reference))
    assert result.report is not None
    hard = next(c for c in result.report.checks if c.check_id == "non-inferiority:hard")
    assert hard.verdict is Verdict.FAIL
    assert result.report.verdict is Verdict.FAIL
    assert result.blocking
    assert result.outcome == "fail"


def test_inconclusive_blocks_only_when_the_policy_says_so(workspace: Workspace) -> None:
    # 40 in-dist cases with no disagreement at all: still too few to rule out 3 p.p.
    reference = synthetic_run(workspace.cases, synthetic_config(accuracy=GOOD, fraction=0.2), "x")
    candidate = synthetic_run(
        workspace.cases, synthetic_config(accuracy=GOOD, fraction=0.2, seed=9, correlation=0.8), "x"
    )
    ctx = _context(workspace, candidate, reference, stage=STAGE_EXPERIMENTAL)
    engine = GateEngine.standard(workspace.spec, workspace.policy)
    report = engine.evaluate(ctx)
    assert report.verdict is Verdict.INCONCLUSIVE
    assert report.blocking
    warn = replace(
        workspace.policy, regression=replace(workspace.policy.regression, on_inconclusive="warn")
    )
    relaxed = engine.evaluate(
        _context(workspace, candidate, reference, stage=STAGE_EXPERIMENTAL, policy=warn)
    )
    assert relaxed.verdict is Verdict.INCONCLUSIVE
    assert not relaxed.blocking
    assert report.enforced_failures()


def test_compatibility_refuses_a_different_case_set(workspace: Workspace) -> None:
    reference = synthetic_run(workspace.cases, synthetic_config(), workspace.dataset_sha256)
    sampled = synthetic_run(
        workspace.cases, synthetic_config(fraction=0.5), workspace.dataset_sha256
    )
    result = CompatibilityCheck().evaluate(
        _context(workspace, sampled, reference, stage=STAGE_EXPERIMENTAL)
    )
    assert result.verdict is Verdict.FAIL
    assert "conjunto de casos" in result.summary
    other_data = synthetic_run(workspace.cases, synthetic_config(), "another-dataset")
    moved = CompatibilityCheck().evaluate(
        _context(workspace, other_data, reference, stage=STAGE_EXPERIMENTAL)
    )
    assert "dataset mudou" in moved.summary
    renamed = synthetic_run(
        workspace.cases, synthetic_config(name="other"), workspace.dataset_sha256
    )
    foreign = CompatibilityCheck().evaluate(
        _context(workspace, renamed, reference, stage=STAGE_EXPERIMENTAL)
    )
    assert "outro candidato" in foreign.summary


def test_first_evaluation_depends_on_the_stage(workspace: Workspace) -> None:
    run = synthetic_run(workspace.cases, synthetic_config(accuracy=GOOD), workspace.dataset_sha256)
    experimental = CompatibilityCheck().evaluate(
        _context(workspace, run, None, stage=STAGE_EXPERIMENTAL)
    )
    assert (experimental.verdict, experimental.enforced) == (Verdict.PASS, False)
    production = CompatibilityCheck().evaluate(
        _context(workspace, run, None, stage=STAGE_PRODUCTION)
    )
    assert (production.verdict, production.enforced) == (Verdict.FAIL, True)
    ni = NonInferiorityCheck(SPLIT_HARD).evaluate(
        _context(workspace, run, None, stage=STAGE_PRODUCTION)
    )
    assert (ni.verdict, ni.enforced) == (Verdict.PASS, False)


def test_no_paired_cases_is_inconclusive(workspace: Workspace) -> None:
    in_only = [c for c in workspace.cases if c.split == SPLIT_IN]
    reference = synthetic_run(in_only, synthetic_config(), "x")
    run = synthetic_run(workspace.cases, synthetic_config(), "x")
    ni = NonInferiorityCheck(SPLIT_HARD).evaluate(
        _context(workspace, run, reference, stage=STAGE_EXPERIMENTAL)
    )
    assert (ni.verdict, ni.enforced) == (Verdict.INCONCLUSIVE, True)


def _requirement(**overrides: object) -> Requirement:
    base: dict[str, object] = {
        "id": "REQ-900",
        "title": "t",
        "rationale": "",
        "split": SPLIT_HARD,
        "metric": "accuracy",
        "op": ">=",
        "threshold": 0.8,
        "evidence": "point",
        "enforcement": "block",
        "label": None,
    }
    base.update(overrides)
    return Requirement(**base)


def test_requirements_compare_the_chosen_evidence(workspace: Workspace) -> None:
    run = synthetic_run(workspace.cases, synthetic_config(accuracy=GOOD), "x")
    ctx = _context(workspace, run, None, stage=STAGE_PRODUCTION)
    measured = ctx.metrics[SPLIT_HARD].accuracy
    # Same threshold, three evidence levels: only the point estimate clears it.
    point = RequirementCheck(_requirement(threshold=measured)).evaluate(ctx)
    lower = RequirementCheck(_requirement(threshold=measured, evidence="lower")).evaluate(ctx)
    upper = RequirementCheck(_requirement(op="<=", threshold=measured, evidence="upper")).evaluate(
        ctx
    )
    assert point.verdict is Verdict.PASS and point.enforced
    assert lower.details["compared"] < lower.details["value"]
    assert lower.verdict is Verdict.FAIL
    assert upper.details["compared"] > upper.details["value"]
    assert upper.verdict is Verdict.FAIL
    assert point.details["status"] == "met"
    assert lower.details["status"] == "not_met"
    report_only = RequirementCheck(_requirement(enforcement="report")).evaluate(ctx)
    assert not report_only.enforced
    experimental = RequirementCheck(_requirement()).evaluate(
        _context(workspace, run, None, stage=STAGE_EXPERIMENTAL)
    )
    assert not experimental.enforced


def test_requirement_without_evidence(workspace: Workspace) -> None:
    in_only = [c for c in workspace.cases if c.split == SPLIT_IN]
    run = synthetic_run(in_only, synthetic_config(), "x")
    ctx = _context(workspace, run, None, stage=STAGE_PRODUCTION)
    strict = RequirementCheck(_requirement()).evaluate(ctx)
    assert strict.verdict is Verdict.FAIL
    assert strict.details["status"] == "no_evidence"
    lenient_policy = replace(workspace.policy, require_evidence=False)
    lenient = RequirementCheck(_requirement()).evaluate(
        _context(workspace, run, None, stage=STAGE_PRODUCTION, policy=lenient_policy)
    )
    assert lenient.verdict is Verdict.INCONCLUSIVE


def test_report_serialises(workspace: Workspace) -> None:
    run = synthetic_run(workspace.cases, synthetic_config(accuracy=GOOD), workspace.dataset_sha256)
    result = assess(workspace, synthetic_config(accuracy=GOOD), run, _reference(run))
    assert result.report is not None
    data = result.report.to_dict()
    assert data["spec"]["id"] == workspace.spec.id
    assert data["reference_fingerprint"] == run.fingerprint
    assert {c["check_id"] for c in data["checks"]} >= {
        "compatibility",
        "non-inferiority:hard",
        "REQ-001",
    }
    summary = result.summary_dict()
    assert summary["has_reference"] is True
    assert len(summary["trace"]) == len(workspace.spec.requirements)
