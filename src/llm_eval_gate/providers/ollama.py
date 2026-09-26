"""Adapter for a local Ollama server (`/api/chat`, non-streaming, structured output).

Local on purpose: the gate has to be verifiable by anyone who clones the repo, and a
number that needs somebody's API key to reproduce is a number nobody can check.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from llm_eval_gate.providers.base import Completion, CompletionRequest, ProviderError
from llm_eval_gate.providers.http import Transport, post_json, validate_base_url

DEFAULT_BASE_URL = "http://127.0.0.1:11434"


class OllamaProvider:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout_s: float = 120.0,
        keep_alive: str = "15m",
        transport: Transport = post_json,
        clock: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self._base_url = validate_base_url(base_url)
        self._timeout_s = timeout_s
        self._keep_alive = keep_alive
        self._transport = transport
        self._clock = clock

    @property
    def name(self) -> str:
        return "ollama"

    def complete(self, request: CompletionRequest) -> Completion:
        payload: dict[str, Any] = {
            "model": request.model,
            "stream": False,
            "keep_alive": self._keep_alive,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "options": {
                "temperature": request.temperature,
                "seed": request.seed,
                "num_predict": request.max_tokens,
            },
        }
        if request.schema is not None:
            payload["format"] = dict(request.schema)
        started = self._clock()
        data = self._transport(f"{self._base_url}/api/chat", payload, {}, self._timeout_s)
        latency_ms = (self._clock() - started) / 1e6
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderError("ollama response has no message.content", transient=False)
        return Completion(
            text=content,
            tokens_in=int(data.get("prompt_eval_count") or 0),
            tokens_out=int(data.get("eval_count") or 0),
            latency_ms=latency_ms,
        )
