from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from llm_eval_gate.config import (
    ConfigError,
    Prices,
    load_candidate,
    load_candidates,
    parse_candidate,
)
from llm_eval_gate.spec import SpecError, load_policy, load_spec
from llm_eval_gate.stats import METHOD_TANGO

VALID_REQ = """
[[requirement]]
id = "REQ-001"
title = "t"
split = "hard"
metric = "accuracy"
op = ">="
threshold = 0.5
"""


def _spec(tmp_path: Path, body: str, meta: str = '[meta]\nid = "s"\nversion = "1"\n') -> Path:
    path = tmp_path / "requirements.toml"
    path.write_text(meta + body, encoding="utf-8")
    return path


def _policy(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "policy.toml"
    path.write_text(body, encoding="utf-8")
    return path


# --- spec -----------------------------------------------------------------------------


def test_committed_spec_and_policy(root: Path) -> None:
    spec = load_spec(root / "spec" / "requirements.toml")
    assert spec.id == "triage-classifier"
    assert [r.id for r in spec.requirements][:5] == [
        "REQ-001",
        "REQ-002",
        "REQ-003",
        "REQ-004",
        "REQ-005",
    ]
    blocking = {r.id for r in spec.requirements if r.enforcement == "block"}
    assert blocking == {"REQ-001", "REQ-002", "REQ-003", "REQ-004", "REQ-005"}
    assert spec.requirements[0].criterion() == "accuracy[in-dist] >= 0.95 [limite inferior IC95]"
    recall = next(r for r in spec.requirements if r.label == "none")
    assert recall.criterion().startswith("recall(none)[hard]")
    policy = load_policy(root / "spec" / "policy.toml")
    assert policy.regression.method == METHOD_TANGO
    assert policy.regression.margin == 0.03
    assert policy.regression.on_inconclusive == "fail"
    assert policy.require_evidence is True
    assert len(policy.sha256) == 64


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (VALID_REQ.replace('id = "REQ-001"', 'id = "R1"'), "look like REQ-001"),
        (VALID_REQ + VALID_REQ, "duplicated id"),
        (VALID_REQ.replace('split = "hard"', 'split = "train"'), "split must be one of"),
        (VALID_REQ.replace('"accuracy"', '"vibes"'), "metric must be one of"),
        (VALID_REQ.replace('"accuracy"', '"recall"'), "needs a valid `label`"),
        (VALID_REQ + 'label = "cert"\n', "`label` only applies"),
        (VALID_REQ.replace('">="', '">"'), "op must be one of"),
        (VALID_REQ.replace("0.5", '"half"'), "threshold must be a number"),
        (
            VALID_REQ.replace('"accuracy"', '"macro_f1"') + 'evidence = "lower"\n',
            "interval evidence",
        ),
        (VALID_REQ + 'evidence = "median"\n', "evidence must be one of"),
        (VALID_REQ + 'enforcement = "maybe"\n', "enforcement must be one of"),
        (VALID_REQ + "colour = 1\n", "unknown keys"),
        (VALID_REQ.replace('title = "t"', 'title = " "'), "title is required"),
    ],
)
def test_bad_requirements_are_rejected(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(SpecError, match=message):
        load_spec(_spec(tmp_path, body))


def test_spec_structure_errors(tmp_path: Path) -> None:
    with pytest.raises(SpecError, match="missing \\[meta\\]"):
        load_spec(_spec(tmp_path, VALID_REQ, meta=""))
    with pytest.raises(SpecError, match="at least one"):
        load_spec(_spec(tmp_path, ""))
    with pytest.raises(SpecError, match="invalid TOML"):
        load_spec(_spec(tmp_path, "[[requirement"))
    with pytest.raises(SpecError, match="must be a table"):
        load_spec(_spec(tmp_path, "", meta='requirement = [1]\n[meta]\nid = "s"\n'))


def test_spec_errors_are_all_reported_at_once(tmp_path: Path) -> None:
    body = VALID_REQ.replace('"hard"', '"x"').replace('">="', '"?"')
    with pytest.raises(SpecError) as info:
        load_spec(_spec(tmp_path, body))
    assert len(info.value.problems) == 2


BASE_POLICY = "[regression]\nalpha = 0.05\nmargin = 0.03\n"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[regression]\nalpha = 0.6\nmargin = 0.03\n", "alpha"),
        ("[regression]\nalpha = 0.05\nmargin = 1.0\n", "margin"),
        (BASE_POLICY + "resamples = 5\n", "resamples"),
        (BASE_POLICY + 'seed = "x"\n', "seed"),
        (BASE_POLICY + 'splits = ["train"]\n', "splits"),
        (BASE_POLICY + 'on_inconclusive = "ignore"\n', "on_inconclusive"),
        (BASE_POLICY + 'method = "wald"\n', "method"),
        (BASE_POLICY + "margn = 0.1\n", "unknown keys"),
        (BASE_POLICY + '[coverage]\nrequire_evidence = "yes"\n', "require_evidence"),
        ("[other]\n", "missing \\[regression\\]"),
    ],
)
def test_bad_policies_are_rejected(tmp_path: Path, body: str, message: str) -> None:
    with pytest.raises(SpecError, match=message):
        load_policy(_policy(tmp_path, body))


def test_policy_defaults(tmp_path: Path) -> None:
    policy = load_policy(_policy(tmp_path, BASE_POLICY))
    assert policy.regression.method == METHOD_TANGO
    assert policy.regression.splits == ("in-dist", "hard")
    assert policy.regression.resamples == 2000


# --- candidates -----------------------------------------------------------------------


def test_committed_candidates(root: Path) -> None:
    configs = {c.name: c for c in load_candidates(root / "evals" / "candidates")}
    assert set(configs) == {"ollama-qwen2.5-0.5b", "rules-signature", "synthetic-calibrated"}
    llm = configs["ollama-qwen2.5-0.5b"]
    assert llm.llm is not None
    assert llm.llm.temperature == 0.2
    assert llm.repeats == 3
    assert llm.sample.fraction == 0.5
    assert llm.budget.max_calls == 1000
    assert configs["rules-signature"].stage == "production"
    synthetic = configs["synthetic-calibrated"].synthetic
    assert synthetic is not None
    assert synthetic.accuracy == {"in-dist": 0.97, "hard": 0.81}


def _candidate(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "candidate": {"name": "x", "stage": "experimental"},
        "classifier": {"kind": "synthetic", "accuracy": {"hard": 0.8}},
    }
    for key, value in overrides.items():
        data[key] = value
    return data


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (_candidate(extra={}), "unknown keys"),
        (_candidate(candidate={"name": "Bad Name", "stage": "experimental"}), "must match"),
        (_candidate(candidate={"name": "x", "stage": "beta"}), "stage must be one of"),
        (
            _candidate(candidate={"name": "x", "stage": "experimental", "owner": "me"}),
            "unknown keys",
        ),
        (_candidate(classifier={"kind": "magic"}), "kind must be one of"),
        (_candidate(classifier={"kind": "synthetic"}), "missing \\[accuracy\\]"),
        (
            _candidate(classifier={"kind": "synthetic", "accuracy": {"train2": 0.5}}),
            "accuracy split",
        ),
        (_candidate(classifier={"kind": "synthetic", "accuracy": {"hard": 1.5}}), "within"),
        (
            _candidate(classifier={"kind": "synthetic", "accuracy": {"hard": True}}),
            "must be a number",
        ),
        (
            _candidate(classifier={"kind": "synthetic", "accuracy": {"hard": 0.5}, "repeats": 0}),
            "within",
        ),
        (
            _candidate(classifier={"kind": "synthetic", "accuracy": {"hard": 0.5}, "repeats": 1.5}),
            "integer",
        ),
        (
            _candidate(classifier={"kind": "rules", "model": "../outside.json"}),
            "relative to the project root",
        ),
        (_candidate(classifier={"kind": "rules", "model": "m.json", "extra": 1}), "unknown keys"),
        (_candidate(classifier={"kind": "llm", "provider": "ollama"}), "`model` must be"),
        (_candidate(sample={"fraction": 0.0}), "within"),
        (_candidate(prices={"input_per_mtok_usd": -1}), "within"),
        (_candidate(budget={"max_calls": 0}), "within"),
        (_candidate(classifier=[1]), "must be a table"),
        ({"classifier": {"kind": "rules"}}, "missing \\[candidate\\]"),
    ],
)
def test_bad_candidates_are_rejected(data: dict[str, Any], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_candidate(data)


def test_llm_candidate_parses_every_setting() -> None:
    config = parse_candidate(
        {
            "candidate": {"name": "gpt-x", "stage": "experimental", "description": "d"},
            "classifier": {
                "kind": "llm",
                "provider": "openai-compatible",
                "model": "gpt-x",
                "prompt": "prompts/p.toml",
                "cassette": "evals/cassettes/c.jsonl",
                "temperature": 0.7,
                "seed": 3,
                "max_tokens": 128,
                "timeout_s": 30,
                "base_url": "http://localhost:8000",
                "api_key_env": "KEY",
                "repeats": 2,
            },
            "budget": {"max_calls": 10, "max_tokens": 1000},
        }
    )
    assert config.llm is not None
    assert (config.llm.base_url, config.llm.api_key_env, config.repeats) == (
        "http://localhost:8000",
        "KEY",
        2,
    )
    assert config.budget.max_tokens == 1000


def test_file_name_must_match_candidate(tmp_path: Path) -> None:
    path = tmp_path / "other.toml"
    path.write_text(
        '[candidate]\nname = "x"\nstage = "experimental"\n'
        '[classifier]\nkind = "rules"\nmodel = "m.json"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="file name must match"):
        load_candidate(path)
    path.write_text("[candidate", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_candidate(path)


def test_prices() -> None:
    assert Prices(0.10, 0.40).cost(1_000_000, 500_000) == pytest.approx(0.30)
    assert Prices().cost(10, 10) == 0.0
