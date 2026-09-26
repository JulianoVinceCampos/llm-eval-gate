"""Classifiers under test.

Three implementations of one protocol (Strategy): the deterministic rule baseline, an LLM
behind any Provider, and a synthetic model whose true accuracy is known by construction.
The runner, the metrics and the gate never learn which one they are measuring.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from llm_eval_gate.baseline.signature import LeakageError, SignatureModel
from llm_eval_gate.baseline.signature import classify as classify_signature
from llm_eval_gate.domain import SPLIT_TRAIN, Case, Outcome, Prediction
from llm_eval_gate.labels import Label, all_labels
from llm_eval_gate.parsing import output_schema, parse_output
from llm_eval_gate.prompt import PromptTemplate
from llm_eval_gate.providers.base import CompletionRequest, Provider
from llm_eval_gate.rng import unit_hash

Clock = Callable[[], int]


class Classifier(Protocol):
    @property
    def descriptor(self) -> Mapping[str, Any]: ...

    @property
    def deterministic(self) -> bool: ...

    def classify(self, case: Case, repeat: int) -> Outcome: ...


class RulesClassifier:
    """The frozen rule baseline. Deterministic, so the runner scores it once per case."""

    def __init__(self, model: SignatureModel, *, clock: Clock = time.perf_counter_ns) -> None:
        self._model = model
        self._clock = clock

    @property
    def descriptor(self) -> Mapping[str, Any]:
        return {
            "kind": "rules",
            "model_sha256": self._model.sha256,
            "rules_sha256": self._model.rules_sha256,
            "train_sha256": self._model.train_sha256,
            "threshold": self._model.threshold,
        }

    @property
    def deterministic(self) -> bool:
        return True

    def classify(
        self,
        case: Case,
        repeat: int,  # noqa: ARG002 - deterministic: every repeat is the same
    ) -> Outcome:
        if case.split == SPLIT_TRAIN:
            raise LeakageError("the baseline cannot be scored on the split it was fitted on")
        started = self._clock()
        explanation = classify_signature(self._model, case.text)
        latency_ms = (self._clock() - started) / 1e6
        return Outcome(Prediction(explanation.label, ", ".join(explanation.matched)), latency_ms)


class LLMClassifier:
    """Prompt -> provider -> strict parser. `provider_label` names the real backend.

    The label comes from configuration, not from the provider object, because under replay
    the object is a cassette reader while the evidence still belongs to the live model.
    """

    def __init__(
        self,
        provider: Provider,
        prompt: PromptTemplate,
        *,
        provider_label: str,
        model: str,
        temperature: float,
        seed: int,
        max_tokens: int,
    ) -> None:
        self._provider = provider
        self._prompt = prompt
        self._provider_label = provider_label
        self._model = model
        self._temperature = temperature
        self._seed = seed
        self._max_tokens = max_tokens
        self._schema = output_schema()

    @property
    def descriptor(self) -> Mapping[str, Any]:
        return {
            "kind": "llm",
            "provider": self._provider_label,
            "model": self._model,
            "prompt_id": self._prompt.id,
            "prompt_sha256": self._prompt.sha256,
            "temperature": self._temperature,
            "seed": self._seed,
            "max_tokens": self._max_tokens,
        }

    @property
    def deterministic(self) -> bool:
        return False

    def request_for(self, case: Case, repeat: int) -> CompletionRequest:
        system, user = self._prompt.render(case.text)
        # One seed per repeat: repeats are independent samples of the configured sampler,
        # which is what makes the measured instability mean something.
        return CompletionRequest(
            model=self._model,
            system=system,
            user=user,
            temperature=self._temperature,
            seed=self._seed + repeat,
            max_tokens=self._max_tokens,
            schema=self._schema,
        )

    def classify(self, case: Case, repeat: int) -> Outcome:
        completion = self._provider.complete(self.request_for(case, repeat))
        return Outcome(
            parse_output(completion.text),
            completion.latency_ms,
            completion.tokens_in,
            completion.tokens_out,
        )


@dataclass(frozen=True, slots=True)
class SyntheticProfile:
    """A model whose accuracy per split is set by hand. Used to test the gate itself."""

    accuracy: Mapping[str, float]
    instability: float = 0.0
    invalid_rate: float = 0.0
    correlation: float = 1.0
    latency_ms: float = 400.0
    seed: int = 0

    def descriptor(self) -> dict[str, Any]:
        return {
            "kind": "synthetic",
            "accuracy": dict(sorted(self.accuracy.items())),
            "instability": self.instability,
            "invalid_rate": self.invalid_rate,
            "correlation": self.correlation,
            "latency_ms": self.latency_ms,
            "seed": self.seed,
        }


class SyntheticClassifier:
    """Correct on a case when its latent difficulty falls under the configured accuracy.

    Every candidate shares the same latent difficulty per case (`correlation` of them do),
    so two synthetic models disagree the way two real model versions do: mostly on the same
    hard cases, sometimes in both directions. That paired structure is what the gate's
    statistics are built for, and it is why this model is a fair test of the gate.
    """

    def __init__(self, profile: SyntheticProfile) -> None:
        self._profile = profile
        self._labels = all_labels()

    @property
    def descriptor(self) -> Mapping[str, Any]:
        return self._profile.descriptor()

    @property
    def deterministic(self) -> bool:
        return self._profile.instability == 0.0 and self._profile.invalid_rate == 0.0

    def _latent(self, case: Case) -> float:
        seed = self._profile.seed
        if unit_hash("synthetic-correlation", seed, case.id) < self._profile.correlation:
            return unit_hash("synthetic-difficulty", case.id)
        return unit_hash("synthetic-own-difficulty", seed, case.id)

    def classify(self, case: Case, repeat: int) -> Outcome:
        profile = self._profile
        accuracy = profile.accuracy.get(case.split, 0.0)
        jitter = unit_hash("synthetic-jitter", profile.seed, case.id, repeat) - 0.5
        latency = profile.latency_ms * (
            0.6 + 0.8 * unit_hash("synthetic-latency", profile.seed, case.id, repeat)
        )
        tokens_in = 350 + len(case.text) // 4
        if unit_hash("synthetic-invalid", profile.seed, case.id, repeat) < profile.invalid_rate:
            return Outcome(Prediction(None), latency, tokens_in, 12)
        if self._latent(case) + jitter * profile.instability < accuracy:
            return Outcome(Prediction(case.label, "synthetic"), latency, tokens_in, 18)
        others = [label for label in self._labels if label is not case.label]
        wrong: Label = others[
            int(unit_hash("synthetic-wrong", profile.seed, case.id) * len(others))
        ]
        return Outcome(Prediction(wrong, "synthetic"), latency, tokens_in, 18)
