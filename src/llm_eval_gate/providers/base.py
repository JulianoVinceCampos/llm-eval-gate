"""The Provider port: one request in, one completion out.

Everything that talks to a model implements this protocol, and so does everything that
wraps one (replay, recording, retry, budget). The classifier never knows which of them it
is holding, which is what lets the same code path run live on a laptop, from a cassette in
CI, and under a fake in the tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from llm_eval_gate.jsonio import sha256_obj


class ProviderError(RuntimeError):
    """A provider could not produce a completion. `transient` says whether to retry."""

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


class CassetteMissError(ProviderError):
    """Replay found no recorded answer for this exact request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, transient=False)


class BudgetExceededError(ProviderError):
    """The run hit its call or token ceiling. Stops the run instead of billing past it."""

    def __init__(self, message: str) -> None:
        super().__init__(message, transient=False)


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    model: str
    system: str
    user: str
    temperature: float
    seed: int
    max_tokens: int
    schema: Mapping[str, Any] | None = None

    def key(self) -> str:
        """Content address of the request. Any change in any field is a different key."""
        return sha256_obj(
            {
                "model": self.model,
                "system": self.system,
                "user": self.user,
                "temperature": self.temperature,
                "seed": self.seed,
                "max_tokens": self.max_tokens,
                "schema": dict(self.schema) if self.schema is not None else None,
            }
        )


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    tokens_in: int
    tokens_out: int
    latency_ms: float


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    def complete(self, request: CompletionRequest) -> Completion: ...
