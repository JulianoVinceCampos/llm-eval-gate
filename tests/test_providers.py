"""Providers against a real local HTTP server: the adapters are only as good as their IO."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from llm_eval_gate.providers.base import (
    BudgetExceededError,
    CassetteMissError,
    Completion,
    CompletionRequest,
    ProviderError,
)
from llm_eval_gate.providers.http import post_json, validate_base_url
from llm_eval_gate.providers.ollama import OllamaProvider
from llm_eval_gate.providers.openai_compat import OpenAICompatibleProvider
from llm_eval_gate.providers.replay import Cassette, RecordingProvider, ReplayProvider
from llm_eval_gate.providers.resilience import BudgetGuard, RetryingProvider
from llm_eval_gate.rng import Rng

REQUEST = CompletionRequest(
    model="m",
    system="s",
    user="u",
    temperature=0.2,
    seed=7,
    max_tokens=64,
    schema={"type": "object"},
)


class _Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict[str, Any]]] = []

    def log_message(self, format: str, *args: Any) -> None:
        return None

    def _reply(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        _Handler.seen.append({"path": self.path, "payload": payload, "headers": dict(self.headers)})
        if self.path == "/api/chat":
            answer = {
                "message": {"content": '{"label": "cert"}'},
                "prompt_eval_count": 120,
                "eval_count": 9,
            }
            self._reply(200, json.dumps(answer).encode())
        elif self.path == "/v1/chat/completions":
            answer = {
                "choices": [{"message": {"content": '{"label": "acl"}'}}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 7},
            }
            self._reply(200, json.dumps(answer).encode())
        elif self.path == "/empty/api/chat":
            self._reply(200, b'{"message": {}}')
        elif self.path == "/empty/v1/chat/completions":
            self._reply(200, b'{"choices": []}')
        elif self.path.startswith("/status/"):
            self._reply(int(self.path.rsplit("/", 1)[1]), b'{"error": "x"}')
        elif self.path == "/text":
            self._reply(200, b"not json", "text/plain")
        elif self.path == "/array":
            self._reply(200, b"[1, 2]")
        elif self.path == "/big":
            self._reply(200, b'{"a": "' + b"x" * 5000 + b'"}')
        else:
            self._reply(404, b"{}")


@pytest.fixture(scope="module")
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --- transport ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["ftp://host", "file:///etc/passwd", "http://", "https://user:pass@host", "host:11434"]
)
def test_only_plain_http_urls_are_accepted(url: str) -> None:
    with pytest.raises(ValueError, match="provider URL"):
        validate_base_url(url)


def test_base_url_is_normalised() -> None:
    assert validate_base_url("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"


def test_post_json_round_trip(server: str) -> None:
    data = post_json(f"{server}/api/chat", {"a": 1}, {"X-Test": "1"}, 5.0)
    assert data["eval_count"] == 9
    assert _Handler.seen[-1]["headers"]["X-Test"] == "1"
    assert _Handler.seen[-1]["headers"]["User-Agent"].startswith("llm-eval-gate/")


@pytest.mark.parametrize(
    ("status", "transient"), [(500, True), (503, True), (429, True), (400, False), (404, False)]
)
def test_http_errors_say_whether_to_retry(server: str, status: int, transient: bool) -> None:
    with pytest.raises(ProviderError) as info:
        post_json(f"{server}/status/{status}", {}, {"Authorization": "Bearer secret-value"}, 5.0)
    assert info.value.transient is transient
    assert f"HTTP {status}" in str(info.value)
    assert "secret-value" not in str(info.value)


@pytest.mark.parametrize(
    ("path", "message"), [("/text", "not JSON"), ("/array", "not a JSON object")]
)
def test_malformed_bodies_are_errors(server: str, path: str, message: str) -> None:
    with pytest.raises(ProviderError, match=message) as info:
        post_json(f"{server}{path}", {}, {}, 5.0)
    assert info.value.transient is False


def test_oversized_bodies_are_refused(server: str) -> None:
    with pytest.raises(ProviderError, match="exceeds"):
        post_json(f"{server}/big", {}, {}, 5.0, max_bytes=100)


def test_unreachable_server_is_transient() -> None:
    with pytest.raises(ProviderError, match="cannot reach") as info:
        post_json(f"http://127.0.0.1:{_closed_port()}/api/chat", {}, {}, 2.0)
    assert info.value.transient is True


# --- adapters -------------------------------------------------------------------------


def test_ollama_adapter_speaks_the_chat_api(server: str) -> None:
    completion = OllamaProvider(server, timeout_s=5).complete(REQUEST)
    assert completion.text == '{"label": "cert"}'
    assert (completion.tokens_in, completion.tokens_out) == (120, 9)
    assert completion.latency_ms >= 0
    sent = _Handler.seen[-1]["payload"]
    assert sent["stream"] is False
    assert sent["options"] == {"temperature": 0.2, "seed": 7, "num_predict": 64}
    assert sent["format"] == {"type": "object"}
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]


def test_ollama_adapter_rejects_an_empty_answer(server: str) -> None:
    with pytest.raises(ProviderError, match=r"message\.content"):
        OllamaProvider(f"{server}/empty", timeout_s=5).complete(REQUEST)


def test_openai_adapter_sends_the_key_and_the_schema(server: str) -> None:
    provider = OpenAICompatibleProvider(
        server, api_key_env="LEG_KEY", timeout_s=5, environ={"LEG_KEY": "k-123"}
    )
    completion = provider.complete(REQUEST)
    assert completion.text == '{"label": "acl"}'
    assert (completion.tokens_in, completion.tokens_out) == (80, 7)
    seen = _Handler.seen[-1]
    assert seen["headers"]["Authorization"] == "Bearer k-123"
    assert seen["payload"]["response_format"]["json_schema"]["strict"] is True
    assert provider.name == "openai-compatible"


def test_openai_adapter_needs_its_key_and_valid_answers(server: str) -> None:
    with pytest.raises(ProviderError, match="LEG_KEY"):
        OpenAICompatibleProvider(server, api_key_env="LEG_KEY", environ={})
    with pytest.raises(ProviderError, match="choices"):
        OpenAICompatibleProvider(f"{server}/empty", environ={}).complete(REQUEST)

    def no_content(
        url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float
    ) -> dict[str, Any]:
        return {"choices": [{"message": {"content": None}}]}

    with pytest.raises(ProviderError, match=r"message\.content"):
        OpenAICompatibleProvider(server, transport=no_content, environ={}).complete(REQUEST)


# --- resilience -----------------------------------------------------------------------


class Scripted:
    """Provider that fails as scripted, then answers."""

    def __init__(self, failures: list[ProviderError], tokens: int = 10) -> None:
        self.failures = list(failures)
        self.calls = 0
        self.tokens = tokens

    @property
    def name(self) -> str:
        return "scripted"

    def complete(self, request: CompletionRequest) -> Completion:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return Completion('{"label": "none"}', self.tokens, self.tokens, 1.0)


def test_retry_backs_off_on_transient_errors_only() -> None:
    delays: list[float] = []
    inner = Scripted([ProviderError("down", transient=True), ProviderError("down", transient=True)])
    provider = RetryingProvider(
        inner, attempts=3, base_delay_s=0.5, max_delay_s=0.8, sleep=delays.append, rng=Rng("t")
    )
    assert provider.complete(REQUEST).text == '{"label": "none"}'
    assert inner.calls == 3
    assert len(delays) == 2
    assert 0 <= delays[0] <= 0.5
    assert 0 <= delays[1] <= 0.8
    assert provider.name == "scripted"

    permanent = Scripted([ProviderError("bad request", transient=False)])
    with pytest.raises(ProviderError, match="bad request"):
        RetryingProvider(permanent, sleep=delays.append).complete(REQUEST)
    assert permanent.calls == 1


def test_retry_gives_up_after_the_last_attempt() -> None:
    inner = Scripted([ProviderError("down", transient=True)] * 5)
    with pytest.raises(ProviderError, match="down"):
        RetryingProvider(inner, attempts=2, sleep=lambda _: None).complete(REQUEST)
    assert inner.calls == 2
    with pytest.raises(ValueError, match="attempts"):
        RetryingProvider(inner, attempts=0)


def test_budget_trips_before_the_call_that_would_exceed_it() -> None:
    guard = BudgetGuard(Scripted([]), max_calls=2)
    guard.complete(REQUEST)
    guard.complete(REQUEST)
    with pytest.raises(BudgetExceededError, match="call budget"):
        guard.complete(REQUEST)
    tokens = BudgetGuard(Scripted([], tokens=30), max_tokens=100)
    tokens.complete(REQUEST)
    tokens.complete(REQUEST)
    with pytest.raises(BudgetExceededError, match="token budget"):
        tokens.complete(REQUEST)
    assert tokens.calls == 2
    assert tokens.tokens == 120
    assert guard.name == "scripted"


# --- record and replay ----------------------------------------------------------------


def test_request_key_changes_with_every_field() -> None:
    keys = {
        REQUEST.key(),
        CompletionRequest("m2", "s", "u", 0.2, 7, 64, {"type": "object"}).key(),
        CompletionRequest("m", "s2", "u", 0.2, 7, 64, {"type": "object"}).key(),
        CompletionRequest("m", "s", "u2", 0.2, 7, 64, {"type": "object"}).key(),
        CompletionRequest("m", "s", "u", 0.3, 7, 64, {"type": "object"}).key(),
        CompletionRequest("m", "s", "u", 0.2, 8, 64, {"type": "object"}).key(),
        CompletionRequest("m", "s", "u", 0.2, 7, 65, {"type": "object"}).key(),
        CompletionRequest("m", "s", "u", 0.2, 7, 64, None).key(),
    }
    assert len(keys) == 8


def test_record_then_replay(tmp_path: Path) -> None:
    cassette = Cassette()
    recorder = RecordingProvider(Scripted([]), cassette)
    first = recorder.complete(REQUEST)
    assert len(cassette) == 1
    assert recorder.name == "recording(scripted)"
    path = tmp_path / "c.jsonl"
    cassette.save(path)
    # The request digest is stored as `request_sha256`, never as `key`: a secret scanner
    # reads `"key":"<64 hex>"` as a leaked credential and the recording PR would go red.
    row = path.read_text(encoding="utf-8")
    assert f'"request_sha256":"{REQUEST.key()}"' in row
    assert '"key"' not in row
    loaded = Cassette.load(path)
    replay = ReplayProvider(loaded, source="c.jsonl")
    assert replay.complete(REQUEST) == first
    assert replay.name == "replay"
    other = CompletionRequest("m", "s", "changed", 0.2, 7, 64, None)
    with pytest.raises(CassetteMissError, match="Re-record") as info:
        replay.complete(other)
    assert info.value.transient is False
