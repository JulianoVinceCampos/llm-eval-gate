from __future__ import annotations

from pathlib import Path

import pytest

from llm_eval_gate.baseline.rules import RULES_SHA256, extract, known_tokens, tokens
from llm_eval_gate.baseline.signature import (
    THRESHOLD_GRID,
    LeakageError,
    SignatureModel,
    classify,
    fit,
)
from llm_eval_gate.datasets.store import load_manifest, load_split
from llm_eval_gate.domain import SPLIT_HARD, SPLIT_IN, SPLIT_TRAIN, Case
from llm_eval_gate.jsonio import read_json
from llm_eval_gate.labels import Label
from llm_eval_gate.pipeline import Project, fit_baseline


@pytest.fixture(scope="module")
def model(root: Path) -> SignatureModel:
    return SignatureModel.from_dict(read_json(root / "evals" / "models" / "rules-signature.json"))


def test_rule_table_is_the_ported_one() -> None:
    assert len(known_tokens()) == 31
    assert len(set(known_tokens())) == 31
    assert len(RULES_SHA256) == 64


def test_rules_read_both_languages() -> None:
    assert "saturation.pool.exhausted" in tokens("Connection pool at 80/80 on every node")
    assert "saturation.pool.exhausted" in tokens("o pool esgotado em todos os nos")
    assert "lifecycle.cert.expired" in tokens("O certificado da borda expirou ontem")
    assert "resource.memory.exhausted" in tokens("java.lang.OutOfMemoryError: Java heap space")
    assert tokens("tudo normal, nenhum alerta") == frozenset()


def test_extract_keeps_short_evidence() -> None:
    text = (
        "Everything fine for hours. " * 5
        + "Then a deadlock appeared in the ledger table. "
        + "Tail. " * 30
    )
    signal = next(s for s in extract(text) if s.token == "store.lock.contention")
    assert "deadlock" in signal.evidence
    assert len(signal.evidence) <= 160


def test_committed_model_is_fitted_on_train_only(project: Project, model: SignatureModel) -> None:
    manifest = load_manifest(project.root)
    assert model.train_sha256 == manifest["splits"][SPLIT_TRAIN]["sha256"]
    assert model.rules_sha256 == RULES_SHA256
    assert model.threshold in THRESHOLD_GRID
    assert fit_baseline(project, check=True) == []


def test_fit_refuses_other_splits(root: Path) -> None:
    cases = load_split(root, SPLIT_IN)[:5]
    with pytest.raises(LeakageError, match="train"):
        fit(cases, train_sha256="x")
    with pytest.raises(ValueError, match="empty"):
        fit([], train_sha256="x")


def test_baseline_is_perfect_in_distribution_and_drops_on_hard(
    root: Path, model: SignatureModel
) -> None:
    def accuracy(split: str) -> float:
        cases = load_split(root, split)
        return sum(classify(model, c.text).label is c.label for c in cases) / len(cases)

    in_dist, hard = accuracy(SPLIT_IN), accuracy(SPLIT_HARD)
    assert in_dist >= 0.95
    # The whole point of the hard split: vocabulary shift costs the rules dearly.
    assert hard < in_dist - 0.30


def test_classification_is_explained(root: Path, model: SignatureModel) -> None:
    case = next(c for c in load_split(root, SPLIT_IN) if c.label is Label.POOL_LOCK)
    explanation = classify(model, case.text)
    assert explanation.label is Label.POOL_LOCK
    assert explanation.matched
    assert set(explanation.matched) <= set(explanation.observed)
    assert explanation.score >= model.threshold


def test_below_threshold_is_none(model: SignatureModel) -> None:
    explanation = classify(model, "Nothing matched here at all.")
    assert explanation.label is Label.NONE
    assert explanation.matched == ()


def test_model_round_trips(model: SignatureModel) -> None:
    assert SignatureModel.from_dict(model.to_dict()) == model
    assert model.sha256 == SignatureModel.from_dict(model.to_dict()).sha256


def test_fit_is_deterministic_on_a_tiny_split() -> None:
    cases = [
        Case(f"c{i}", SPLIT_TRAIN, Label.CERT, "en", "The TLS certificate expired at the edge.")
        for i in range(3)
    ] + [Case("n", SPLIT_TRAIN, Label.NONE, "en", "A one-off event.")]
    first, second = fit(cases, train_sha256="t"), fit(cases, train_sha256="t")
    assert first == second
    assert first.signatures["cert"] == ("lifecycle.cert.expired",)
