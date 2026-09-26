from __future__ import annotations

import io
from pathlib import Path

import pytest

from helpers import KeywordProvider, keyword_factory, synthetic_config, synthetic_run
from llm_eval_gate.baseline.signature import LeakageError, SignatureModel
from llm_eval_gate.classifiers import LLMClassifier, RulesClassifier, SyntheticClassifier
from llm_eval_gate.config import CandidateConfig, ConfigError, LLMSettings, load_candidates
from llm_eval_gate.domain import SPLIT_HARD, SPLIT_IN, SPLIT_TRAIN, Case, Observation, RunRecord
from llm_eval_gate.factory import PendingEvidenceError, ProviderMode, build_classifier
from llm_eval_gate.jsonio import read_json
from llm_eval_gate.labels import INVALID, Label
from llm_eval_gate.pipeline import Project, Workspace
from llm_eval_gate.prompt import load_prompt
from llm_eval_gate.runner import EvalRunner, NullListener, ProgressPrinter, stratified_sample


def _config(project: Project, name: str) -> CandidateConfig:
    return next(c for c in load_candidates(project.candidates_dir) if c.name == name)


def _cassette(project: Project, config: CandidateConfig) -> Path:
    assert config.llm is not None
    return project.root / config.llm.cassette


# --- factory --------------------------------------------------------------------------


def test_factory_builds_every_committed_kind(project: Project) -> None:
    rules = build_classifier(_config(project, "rules-signature"), project.root)
    assert isinstance(rules.classifier, RulesClassifier)
    synthetic = build_classifier(_config(project, "synthetic-calibrated"), project.root)
    assert isinstance(synthetic.classifier, SyntheticClassifier)


def test_replay_without_cassette_is_pending_evidence(project: Project) -> None:
    config = _config(project, "ollama-qwen2.5-0.5b")
    assert not _cassette(project, config).exists()
    with pytest.raises(PendingEvidenceError, match="no cassette"):
        build_classifier(config, project.root, ProviderMode.REPLAY)


def test_record_writes_a_cassette_that_replay_reads(
    tmp_project: Project, workspace: Workspace
) -> None:
    config = _config(tmp_project, "ollama-qwen2.5-0.5b")
    cases = list(workspace.cases)[:12]
    built = build_classifier(
        config, tmp_project.root, ProviderMode.RECORD, providers={"ollama": keyword_factory}
    )
    recorded = EvalRunner(built.classifier).run(config, cases, workspace.dataset_sha256)
    built.persist()
    assert _cassette(tmp_project, config).exists()
    assert built.budget is not None and built.budget.calls == len(recorded.observations)

    replayed_built = build_classifier(config, tmp_project.root, ProviderMode.REPLAY)
    replayed = EvalRunner(replayed_built.classifier).run(config, cases, workspace.dataset_sha256)
    assert replayed == recorded
    assert replayed.fingerprint == recorded.fingerprint
    assert replayed.classifier["provider"] == "ollama"


def test_live_mode_is_budgeted(tmp_project: Project) -> None:
    config = _config(tmp_project, "ollama-qwen2.5-0.5b")
    built = build_classifier(
        config, tmp_project.root, ProviderMode.LIVE, providers={"ollama": keyword_factory}
    )
    assert built.budget is not None
    assert built.cassette is None
    built.persist()
    assert not _cassette(tmp_project, config).exists()


def test_unknown_provider_and_missing_urls(project: Project) -> None:
    config = _config(project, "ollama-qwen2.5-0.5b")
    with pytest.raises(ConfigError, match="unknown provider"):
        build_classifier(config, project.root, ProviderMode.LIVE, providers={})
    from llm_eval_gate.factory import _ollama, _openai

    settings = LLMSettings("ollama", "m", "p.toml", "c.jsonl")
    provider = _ollama(settings, {"OLLAMA_HOST": "192.0.2.10:11434"})
    assert provider.name == "ollama"
    with pytest.raises(ConfigError, match="base_url"):
        _openai(LLMSettings("openai-compatible", "m", "p.toml", "c.jsonl"), {})
    assert _openai(
        LLMSettings("openai-compatible", "m", "p", "c"), {"LEG_OPENAI_BASE_URL": "http://h"}
    ).name


# --- classifiers ----------------------------------------------------------------------


def test_rules_classifier_refuses_the_training_split(project: Project) -> None:
    model = SignatureModel.from_dict(read_json(project.model_path))
    ticks = iter(range(0, 10**9, 1_000_000))
    classifier = RulesClassifier(model, clock=lambda: next(ticks))
    case = Case("c1", SPLIT_IN, Label.CERT, "en", "The TLS certificate expired at the edge.")
    outcome = classifier.classify(case, 0)
    assert outcome.latency_ms == pytest.approx(1.0)
    assert classifier.deterministic
    assert classifier.descriptor["model_sha256"] == model.sha256
    with pytest.raises(LeakageError):
        classifier.classify(Case("c2", SPLIT_TRAIN, Label.CERT, "en", "x"), 0)


def test_llm_classifier_uses_one_seed_per_repeat(root: Path) -> None:
    prompt = load_prompt(root / "prompts" / "classify-v1.toml")
    classifier = LLMClassifier(
        KeywordProvider(),
        prompt,
        provider_label="ollama",
        model="m",
        temperature=0.2,
        seed=7,
        max_tokens=96,
    )
    case = Case("c1", SPLIT_HARD, Label.CERT, "en", "The certificate expired.")
    assert classifier.request_for(case, 0).seed == 7
    assert classifier.request_for(case, 2).seed == 9
    assert classifier.request_for(case, 0).key() != classifier.request_for(case, 1).key()
    outcome = classifier.classify(case, 0)
    assert outcome.prediction.label is Label.CERT
    assert classifier.descriptor["prompt_sha256"] == prompt.sha256
    assert not classifier.deterministic


def test_llm_classifier_turns_bad_output_into_invalid(root: Path) -> None:
    prompt = load_prompt(root / "prompts" / "classify-v1.toml")
    classifier = LLMClassifier(
        KeywordProvider(broken_every=1),
        prompt,
        provider_label="x",
        model="m",
        temperature=0.0,
        seed=0,
        max_tokens=96,
    )
    outcome = classifier.classify(Case("c1", SPLIT_HARD, Label.CERT, "en", "x"), 0)
    assert outcome.prediction.label is None
    assert outcome.prediction.value == INVALID


def test_synthetic_accuracy_is_what_it_says(workspace: Workspace) -> None:
    config = synthetic_config(accuracy={"in-dist": 0.9, "hard": 0.6})
    run = synthetic_run(workspace.cases, config, workspace.dataset_sha256)
    labels = {case.id: case for case in workspace.cases}
    for split, target in (("in-dist", 0.9), ("hard", 0.6)):
        observed = [o for o in run.observations if labels[o.case_id].split == split]
        accuracy = sum(o.predicted == labels[o.case_id].label.value for o in observed) / len(
            observed
        )
        assert abs(accuracy - target) < 0.08, (split, accuracy)


def test_synthetic_is_deterministic_and_can_be_unstable(workspace: Workspace) -> None:
    config = synthetic_config(repeats=3, instability=0.3, invalid_rate=0.05, seed=4)
    first = synthetic_run(workspace.cases, config, "x")
    second = synthetic_run(workspace.cases, config, "x")
    assert first == second
    assert first.repeats == 3
    assert any(o.predicted == INVALID for o in first.observations)
    by_case = first.by_case()
    assert any(len({o.predicted for o in obs}) > 1 for obs in by_case.values())


# --- runner ---------------------------------------------------------------------------


def test_deterministic_classifiers_run_once_per_case(workspace: Workspace) -> None:
    config = synthetic_config(repeats=5)
    run = synthetic_run(workspace.cases, config, "x")
    assert run.repeats == 1
    assert len(run.observations) == len(workspace.cases)


def test_stratified_sample_keeps_every_cell(workspace: Workspace) -> None:
    from llm_eval_gate.config import SampleSpec

    picked = stratified_sample(workspace.cases, SampleSpec(fraction=0.05, seed=1))
    cells = {(c.split, c.label) for c in workspace.cases}
    assert {(c.split, c.label) for c in picked} == cells
    assert picked == sorted(picked, key=lambda c: c.id)
    assert stratified_sample(workspace.cases, SampleSpec(fraction=0.05, seed=1)) == picked
    assert stratified_sample(workspace.cases, SampleSpec(fraction=0.05, seed=2)) != picked
    half = stratified_sample(workspace.cases, SampleSpec(fraction=0.5, seed=11))
    assert sum(c.split == SPLIT_IN for c in half) == 100
    assert sum(c.split == SPLIT_HARD for c in half) == 150


def test_progress_is_published_to_listeners(workspace: Workspace) -> None:
    stream = io.StringIO()
    config = synthetic_config(repeats=2, instability=0.2)
    assert config.synthetic is not None
    classifier = SyntheticClassifier(config.synthetic)
    record = EvalRunner(classifier, listener=ProgressPrinter(stream, every=100)).run(
        config, workspace.cases[:60], "x"
    )
    output = stream.getvalue()
    assert "120 classifications" in output
    assert "[synthetic-test] 100/120" in output
    assert "done: 120 observations" in output
    listener = NullListener()
    assert listener.on_start("x", 1) is None
    assert listener.on_observation(record.observations[0], 1, 1) is None
    assert listener.on_finish(record) is None


def test_run_record_round_trips_and_rejects_other_schemas() -> None:
    record = RunRecord(
        candidate="c",
        stage="experimental",
        classifier={"kind": "synthetic"},
        dataset_sha256="d",
        repeats=1,
        observations=(Observation("a", 0, "cert", 1.23456, 10, 2, 0.0000012345678),),
    )
    data = record.to_dict()
    assert data["observations"][0]["latency_ms"] == 1.235
    again = RunRecord.from_dict(data)
    assert again.fingerprint == RunRecord.from_dict(again.to_dict()).fingerprint
    assert again.case_ids == frozenset({"a"})
    from llm_eval_gate.jsonio import SchemaError

    with pytest.raises(SchemaError):
        RunRecord.from_dict({**data, "schema": 99})
