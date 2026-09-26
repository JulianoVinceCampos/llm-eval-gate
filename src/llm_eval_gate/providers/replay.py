"""Record and replay of completions, keyed by the content address of the request.

A cassette is evidence: the exact answers a model gave to the exact requests of a run.
Pull requests replay it, so the gate is fast, deterministic and needs no model and no key.
Change anything in the request (prompt, model, temperature, seed, schema) and the key
changes, replay misses, and the run fails loudly instead of reusing stale answers.

The live recording happens in CI (`live-eval` workflow), so the evidence carries the
provenance of a workflow run and not of somebody's laptop.
"""

from __future__ import annotations

from pathlib import Path

from llm_eval_gate.jsonio import read_jsonl, write_jsonl
from llm_eval_gate.providers.base import (
    CassetteMissError,
    Completion,
    CompletionRequest,
    Provider,
)

# Row field holding the content address of the request. Not called "key" on purpose: a
# secret scanner reads `"key":"<64 hex chars>"` as a leaked API key, and a cassette has one
# such line per recorded answer. The name says what the value is: a digest, not a secret.
ADDRESS_FIELD = "request_sha256"


class Cassette:
    def __init__(self, entries: dict[str, Completion] | None = None) -> None:
        self._entries: dict[str, Completion] = dict(entries or {})

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, key: str) -> Completion | None:
        return self._entries.get(key)

    def put(self, key: str, completion: Completion) -> None:
        self._entries[key] = completion

    @classmethod
    def load(cls, path: Path) -> Cassette:
        entries: dict[str, Completion] = {}
        for row in read_jsonl(path):
            entries[str(row[ADDRESS_FIELD])] = Completion(
                text=str(row["text"]),
                tokens_in=int(row["tokens_in"]),
                tokens_out=int(row["tokens_out"]),
                latency_ms=float(row["latency_ms"]),
            )
        return cls(entries)

    def save(self, path: Path) -> None:
        write_jsonl(
            path,
            (
                {
                    ADDRESS_FIELD: key,
                    "text": completion.text,
                    "tokens_in": completion.tokens_in,
                    "tokens_out": completion.tokens_out,
                    "latency_ms": round(completion.latency_ms, 3),
                }
                for key, completion in sorted(self._entries.items())
            ),
        )


class ReplayProvider:
    """Answers only from the cassette. A miss is an error, never a silent fallback."""

    def __init__(self, cassette: Cassette, *, source: str = "cassette") -> None:
        self._cassette = cassette
        self._source = source

    @property
    def name(self) -> str:
        return "replay"

    def complete(self, request: CompletionRequest) -> Completion:
        completion = self._cassette.get(request.key())
        if completion is None:
            raise CassetteMissError(
                f"no recorded answer in {self._source} for this request; the prompt, model "
                "or parameters changed. Re-record with the live-eval workflow."
            )
        return completion


class RecordingProvider:
    """Decorator: forwards to the real provider and keeps every successful answer."""

    def __init__(self, inner: Provider, cassette: Cassette) -> None:
        self._inner = inner
        self._cassette = cassette

    @property
    def name(self) -> str:
        return f"recording({self._inner.name})"

    def complete(self, request: CompletionRequest) -> Completion:
        completion = self._inner.complete(request)
        self._cassette.put(request.key(), completion)
        return completion
