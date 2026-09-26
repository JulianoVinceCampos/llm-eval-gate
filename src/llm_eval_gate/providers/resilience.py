"""Decorators that keep a live run honest: bounded retries and a hard budget ceiling."""

from __future__ import annotations

import time
from collections.abc import Callable

from llm_eval_gate.providers.base import (
    BudgetExceededError,
    Completion,
    CompletionRequest,
    Provider,
    ProviderError,
)
from llm_eval_gate.rng import Rng


class RetryingProvider:
    """Retry transient failures with capped exponential backoff and full jitter.

    Only transient errors retry (connection refused, timeout, 5xx, 429). A 400 means the
    request is wrong, and asking again only multiplies the cost of being wrong.
    """

    def __init__(
        self,
        inner: Provider,
        *,
        attempts: int = 3,
        base_delay_s: float = 0.5,
        max_delay_s: float = 8.0,
        sleep: Callable[[float], None] = time.sleep,
        rng: Rng | None = None,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self._inner = inner
        self._attempts = attempts
        self._base = base_delay_s
        self._max = max_delay_s
        self._sleep = sleep
        self._rng = rng or Rng("retry-jitter")

    @property
    def name(self) -> str:
        return self._inner.name

    def complete(self, request: CompletionRequest) -> Completion:
        for attempt in range(1, self._attempts + 1):
            try:
                return self._inner.complete(request)
            except ProviderError as error:
                if not error.transient or attempt == self._attempts:
                    raise
                ceiling = min(self._max, self._base * 2 ** (attempt - 1))
                self._sleep(ceiling * self._rng.random())
        raise AssertionError("unreachable")  # pragma: no cover


class BudgetGuard:
    """Circuit breaker on calls and tokens. Trips before the call that would exceed it."""

    def __init__(
        self, inner: Provider, *, max_calls: int | None = None, max_tokens: int | None = None
    ) -> None:
        self._inner = inner
        self._max_calls = max_calls
        self._max_tokens = max_tokens
        self.calls = 0
        self.tokens = 0

    @property
    def name(self) -> str:
        return self._inner.name

    def complete(self, request: CompletionRequest) -> Completion:
        if self._max_calls is not None and self.calls >= self._max_calls:
            raise BudgetExceededError(f"call budget of {self._max_calls} reached")
        if self._max_tokens is not None and self.tokens >= self._max_tokens:
            raise BudgetExceededError(f"token budget of {self._max_tokens} reached")
        completion = self._inner.complete(request)
        self.calls += 1
        self.tokens += completion.tokens_in + completion.tokens_out
        return completion
