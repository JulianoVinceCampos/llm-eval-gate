from __future__ import annotations

from pathlib import Path

import pytest

from llm_eval_gate.calibration import (
    CalibrationResult,
    CalibrationSpec,
    run_calibration,
    simulate_pairs,
)
from llm_eval_gate.config import ConfigError
from llm_eval_gate.gate import Verdict
from llm_eval_gate.jsonio import SchemaError, read_json
from llm_eval_gate.pipeline import Project, Workspace, committed_calibration, run_scenarios
from llm_eval_gate.scenarios import load_scenario
from llm_eval_gate.stats import METHOD_BOOTSTRAP, METHOD_TANGO


def test_every_committed_scenario_produces_its_expected_verdict(workspace: Workspace) -> None:
    outcomes = run_scenarios(workspace)
    assert len(outcomes) == 4
    for outcome in outcomes:
        assert outcome.as_expected, (outcome.scenario.name, outcome.verdict)
    by_name = {o.scenario.name: o for o in outcomes}
    assert by_name["regressao-real"].verdict is Verdict.FAIL
    assert by_name["amostra-pequena"].verdict is Verdict.INCONCLUSIVE
    small = by_name["amostra-pequena"].to_dict()
    assert small["fraction"] == 0.1
    assert all(split["method"] == METHOD_TANGO for split in small["splits"])


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "[reference]\naccuracy = {hard = 0.8}\n[candidate]\naccuracy = {hard = 0.8}\n",
            "missing \\[scenario\\]",
        ),
        ('[scenario]\nname = "x"\ntitle = "t"\nexpected = "pass"\n', "are required"),
        (
            '[scenario]\nname = "x"\ntitle = "t"\nexpected = "pass"\n'
            "[reference]\naccuracy = {hard = 0.8}\n"
            "[candidate]\nacuracy = {hard = 0.8}\n",
            "unknown keys",
        ),
        (
            '[scenario]\nname = "x"\ntitle = "t"\nexpected = "pass"\n[reference]\nseed = 1\n'
            "[candidate]\naccuracy = {hard = 0.8}\n",
            "accuracy",
        ),
    ],
)
def test_malformed_scenarios(tmp_path: Path, body: str, message: str) -> None:
    path = tmp_path / "s.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_scenario(path)


def test_simulation_is_deterministic_and_paired() -> None:
    spec = CalibrationSpec(trials=1, correlation=1.0)
    same = simulate_pairs(spec, 50, 0.0, 0)
    assert same == simulate_pairs(spec, 50, 0.0, 0)
    assert all(p.reference_correct == p.candidate_correct for p in same)
    worse = simulate_pairs(spec, 200, -0.2, 0)
    assert all(p.reference_correct or not p.candidate_correct for p in worse)


def test_small_calibration_separates_no_effect_from_a_large_one() -> None:
    spec = CalibrationSpec(
        trials=40,
        sizes=(300,),
        effects=(0.0, -0.15),
        methods=(METHOD_TANGO, METHOD_BOOTSTRAP),
        resamples=200,
    )
    result = run_calibration(spec)
    assert len(result.cells) == 4
    null = result.cell(METHOD_TANGO, 300, 0.0)
    large = result.cell(METHOD_TANGO, 300, -0.15)
    assert null is not None and large is not None
    assert null.fail_rate == 0.0
    assert large.fail_rate > 0.9
    assert null.passes + null.inconclusive + null.fails == 40
    assert result.cell(METHOD_TANGO, 999, 0.0) is None
    again = CalibrationResult.from_dict(result.to_dict())
    assert again == result


def test_calibration_spec_is_validated() -> None:
    with pytest.raises(ValueError, match="unknown methods"):
        CalibrationSpec(methods=("wald",))
    with pytest.raises(ValueError, match="needs"):
        CalibrationSpec(sizes=())
    with pytest.raises(SchemaError):
        CalibrationResult.from_dict({"schema": 1, "spec": {}, "cells": []})


def test_committed_calibration_backs_the_method_choice(project: Project) -> None:
    calibration, problem = committed_calibration(project)
    assert problem is None and calibration is not None
    data = read_json(project.calibration_path)
    assert data["spec"]["methods"] == ["tango", "agresti-min", "bootstrap"]
    # The decision method keeps false passes at the margin near alpha; the bootstrap does not.
    assert calibration.worst_false_pass(METHOD_TANGO) <= 0.08
    assert calibration.worst_false_pass(METHOD_BOOTSTRAP) >= 0.20
    assert calibration.worst_false_alarm(METHOD_TANGO) <= 0.01
    strong = calibration.cell(METHOD_TANGO, 300, -0.12)
    assert strong is not None and strong.fail_rate >= 0.95
