from __future__ import annotations

from helpers import synthetic_config, synthetic_run
from llm_eval_gate.domain import SPLIT_HARD
from llm_eval_gate.gate import CheckResult, Verdict
from llm_eval_gate.labels import INVALID, Label
from llm_eval_gate.metrics import CaseStat
from llm_eval_gate.pipeline import Workspace, assess
from llm_eval_gate.report import candidate_markdown, ci_markdown, method_name
from llm_eval_gate.results import STATUS_ERROR, STATUS_PENDING, CandidateResult, CIOutcome
from llm_eval_gate.spec import Requirement, Spec
from llm_eval_gate.trace import MAX_CASES, build_trace


def _stat(case_id: str, label: Label, predictions: tuple[str, ...], majority: str) -> CaseStat:
    correct = sum(p == label.value for p in predictions) / len(predictions)
    return CaseStat(
        case_id=case_id,
        split=SPLIT_HARD,
        label=label,
        predictions=predictions,
        correct_rate=correct,
        majority=majority,
        unstable=len(set(predictions)) > 1,
        invalid_rate=sum(p == INVALID for p in predictions) / len(predictions),
    )


STATS = {
    "a": _stat("a", Label.CERT, ("cert", "cert", "cert"), "cert"),
    "b": _stat("b", Label.CERT, ("acl", "acl", INVALID), "acl"),
    "c": _stat("c", Label.ACL, ("cert", "cert", "acl"), "cert"),
    "d": _stat("d", Label.NONE, ("none", "none", "none"), "none"),
}


def _req(rid: str, metric: str, label: str | None = None) -> Requirement:
    return Requirement(rid, rid, "", SPLIT_HARD, metric, ">=", 0.9, "point", "report", label)


def _not_met(rid: str) -> CheckResult:
    return CheckResult(
        rid, "requirement", rid, Verdict.FAIL, False, "x", {"status": "not_met", "n": 4}
    )


def test_trace_lists_the_cases_behind_each_failure() -> None:
    requirements = (
        _req("REQ-101", "accuracy"),
        _req("REQ-102", "recall", "cert"),
        _req("REQ-103", "precision", "cert"),
        _req("REQ-104", "format_valid_rate"),
        _req("REQ-105", "instability_rate"),
        _req("REQ-106", "latency_p95_ms"),
        _req("REQ-107", "macro_f1"),
    )
    spec = Spec("s", "1", "t", requirements, "sha")
    rows = {
        row.requirement.id: row
        for row in build_trace(spec, [_not_met(r.id) for r in requirements], STATS)
    }
    assert rows["REQ-101"].failing_cases == ("b", "c")
    assert rows["REQ-102"].failing_cases == ("b",)
    assert rows["REQ-103"].failing_cases == ("c",)
    assert rows["REQ-104"].failing_cases == ("b",)
    assert rows["REQ-105"].failing_cases == ("b", "c")
    assert rows["REQ-106"].failing_cases == ()
    assert rows["REQ-107"].failing_cases == ("b", "c")
    assert rows["REQ-101"].to_dict()["criterion"] == "accuracy[hard] >= 0.9"


def test_requirement_without_a_check_is_reported_without_evidence() -> None:
    spec = Spec("s", "1", "t", (_req("REQ-201", "accuracy"),), "sha")
    (row,) = build_trace(spec, [], STATS)
    assert (row.status, row.enforced, row.n, row.failing_cases) == ("no_evidence", False, 0, ())


def test_failing_cases_are_capped() -> None:
    many = {f"x{i:02d}": _stat(f"x{i:02d}", Label.CERT, ("acl",), "acl") for i in range(25)}
    spec = Spec("s", "1", "t", (_req("REQ-301", "accuracy"),), "sha")
    (row,) = build_trace(spec, [_not_met("REQ-301")], many)
    assert len(row.failing_cases) == MAX_CASES
    assert row.failing_cases == tuple(sorted(many))[:MAX_CASES]


def test_markdown_for_every_status(workspace: Workspace) -> None:
    config = synthetic_config()
    run = synthetic_run(workspace.cases, config, workspace.dataset_sha256)
    evaluated = assess(workspace, config, run, None)
    pending = CandidateResult(config, STATUS_PENDING, message="sem cassette")
    broken = CandidateResult(config, STATUS_ERROR, message="HTTP 500")
    silent = CandidateResult(config, STATUS_PENDING)
    assert "Acurácia (IC95)" in candidate_markdown(evaluated)
    assert "_sem cassette_" in candidate_markdown(pending)
    assert "_sem execução_" in candidate_markdown(silent)
    assert broken.blocking and broken.outcome == "error"
    report = ci_markdown(CIOutcome((evaluated, pending, broken)))
    assert report.startswith("## llm-eval-gate: REPROVA")
    assert "| synthetic-test | experimental | ERRO | sim |" in report
    assert CIOutcome((evaluated,)).to_dict()["exit_code"] == 0
    assert method_name("tango") == "Score de Tango"
    assert method_name("other") == "other"
