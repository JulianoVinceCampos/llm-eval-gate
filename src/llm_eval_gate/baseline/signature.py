"""Nearest-signature classifier over the rule signals.

Each family gets a signature: the tokens present in at least `min_support` of its train
members. A new incident goes to the family whose signature is closest by Jaccard
similarity, or to `none` when nothing is close enough. The same method postmortem-miner
uses to form patterns, turned into a classifier.

Fitting is allowed on the `train` split only, and the fitted model records the sha256 of
the split it saw. Evaluating it on the data it was fitted on would report training
accuracy as if it were generalisation, which is the most common way a baseline lies.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from llm_eval_gate.baseline.rules import RULES_SHA256, tokens
from llm_eval_gate.domain import SPLIT_TRAIN, Case
from llm_eval_gate.jsonio import sha256_obj
from llm_eval_gate.labels import Label, family_labels

MODEL_VERSION = 1
THRESHOLD_GRID: tuple[float, ...] = tuple(round(0.10 + 0.05 * step, 2) for step in range(11))


class LeakageError(ValueError):
    """Raised when the baseline would be fitted or scored on the wrong split."""


@dataclass(frozen=True, slots=True)
class Explanation:
    label: Label
    score: float
    matched: tuple[str, ...]
    observed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SignatureModel:
    signatures: Mapping[str, tuple[str, ...]]
    threshold: float
    min_support: float
    train_sha256: str
    rules_sha256: str
    version: int = MODEL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "signatures": {key: list(value) for key, value in sorted(self.signatures.items())},
            "threshold": self.threshold,
            "min_support": self.min_support,
            "train_sha256": self.train_sha256,
            "rules_sha256": self.rules_sha256,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SignatureModel:
        return cls(
            signatures={str(k): tuple(str(t) for t in v) for k, v in data["signatures"].items()},
            threshold=float(data["threshold"]),
            min_support=float(data["min_support"]),
            train_sha256=str(data["train_sha256"]),
            rules_sha256=str(data["rules_sha256"]),
            version=int(data.get("version", MODEL_VERSION)),
        )

    @property
    def sha256(self) -> str:
        return sha256_obj(self.to_dict())


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def classify(model: SignatureModel, text: str) -> Explanation:
    observed = tokens(text)
    best_label, best_score, best_signature = Label.NONE, 0.0, frozenset[str]()
    for label in family_labels():
        signature = frozenset(model.signatures.get(label.value, ()))
        if not signature:
            continue
        score = _jaccard(observed, signature)
        # Strict comparison: on a tie the label declared first wins, deterministically.
        if score > best_score:
            best_label, best_score, best_signature = label, score, signature
    if best_score < model.threshold:
        return Explanation(Label.NONE, round(best_score, 6), (), tuple(sorted(observed)))
    return Explanation(
        best_label,
        round(best_score, 6),
        tuple(sorted(observed & best_signature)),
        tuple(sorted(observed)),
    )


def _accuracy(model: SignatureModel, cases: Sequence[Case]) -> float:
    hits = sum(1 for case in cases if classify(model, case.text).label is case.label)
    return hits / len(cases)


def fit(cases: Sequence[Case], *, train_sha256: str, min_support: float = 0.6) -> SignatureModel:
    if not cases:
        raise ValueError("cannot fit on an empty split")
    wrong = sorted({case.split for case in cases if case.split != SPLIT_TRAIN})
    if wrong:
        raise LeakageError(f"the baseline fits on `{SPLIT_TRAIN}` only, got {wrong}")

    signatures: dict[str, tuple[str, ...]] = {}
    for label in family_labels():
        members = [case for case in cases if case.label is label]
        if not members:
            continue
        counts: Counter[str] = Counter()
        for case in members:
            counts.update(tokens(case.text))
        signatures[label.value] = tuple(
            sorted(token for token, n in counts.items() if n / len(members) >= min_support)
        )

    # Threshold chosen on train accuracy alone. Ties go to the higher threshold: when two
    # values explain train equally well, prefer the one more willing to answer `none`.
    best: SignatureModel | None = None
    best_accuracy = -1.0
    for threshold in THRESHOLD_GRID:
        candidate = SignatureModel(signatures, threshold, min_support, train_sha256, RULES_SHA256)
        accuracy = _accuracy(candidate, cases)
        if accuracy >= best_accuracy:
            best, best_accuracy = candidate, accuracy
    assert best is not None
    return best
