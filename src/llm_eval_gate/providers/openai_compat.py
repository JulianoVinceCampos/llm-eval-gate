"""Adapter for any OpenAI-compatible `/v1/chat/completions` endpoint.

Covers hosted APIs and local servers that speak the same protocol (vLLM, llama.cpp
server, LM Studio). Optional: nothing in this repository's CI needs it. The API key is read
from an environment variable named in the candidate config and never stored anywhere else.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from llm_eval_gate.providers.base import Completion, CompletionRequest, ProviderError
from llm_eval_gate.providers.http import Transport, post_json, validate_base_url


class OpenAICompatibleProvider:
    def __init__(
        self,
        base_url: str,
        *,
        api_key_env: str | None = None,
        timeout_s: float = 60.0,
        transport: Transport = post_json,
        clock: Callable[[], int] = time.perf_counter_ns,
        environ: dict[str, str] | None = None,
    ) -> None:
        self._base_url = validate_base_url(base_url)
        self._timeout_s = timeout_s
        self._transport = transport
        self._clock = clock
        source = environ if environ is not None else dict(os.environ)
        self._api_key = source.get(api_key_env, "") if api_key_env else ""
        if api_key_env and not self._api_key:
            raise ProviderError(f"environment variable {api_key_env} is empty", transient=False)

    @property
    def name(self) -> str:
        return "openai-compatible"

    def complete(self, request: CompletionRequest) -> Completion:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "seed": request.seed,
            "max_tokens": request.max_tokens,
        }
        if request.schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "triage_label",
                    "schema": dict(request.schema),
                    "strict": True,
                },
            }
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        started = self._clock()
        data = self._transport(
            f"{self._base_url}/v1/chat/completions", payload, headers, self._timeout_s
        )
        latency_ms = (self._clock() - started) / 1e6
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderError("response has no choices", transient=False)
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderError("response has no message.content", transient=False)
        raw_usage = data.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        return Completion(
            text=content,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            latency_ms=latency_ms,
        )
