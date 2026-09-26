"""Accepted reference runs: the baseline a candidate must not regress against.

Called "reference" and not "baseline" on purpose: the baseline is the rule classifier, the
reference is a run someone accepted. A reference only changes through `accept`, which
records who-knows-when and from which commit, and lands as a reviewed diff in a pull
request. It never updates itself because a run happened to be greener.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_eval_gate.domain import RunRecord
from llm_eval_gate.jsonio import SchemaError, read_json, write_json

REFERENCE_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class Reference:
    run: RunRecord
    accepted_at: str
    source_commit: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": REFERENCE_SCHEMA,
            "accepted_at": self.accepted_at,
            "source_commit": self.source_commit,
            "note": self.note,
            "run": self.run.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Reference:
        if int(data.get("schema", 0)) != REFERENCE_SCHEMA:
            raise SchemaError(f"unsupported reference schema, expected {REFERENCE_SCHEMA}")
        return cls(
            run=RunRecord.from_dict(data["run"]),
            accepted_at=str(data["accepted_at"]),
            source_commit=str(data["source_commit"]),
            note=str(data.get("note", "")),
        )


def reference_dir(root: Path) -> Path:
    return root / "evals" / "reference"


def reference_path(root: Path, candidate: str) -> Path:
    return reference_dir(root) / f"{candidate}.json"


def load_reference(root: Path, candidate: str) -> Reference | None:
    path = reference_path(root, candidate)
    if not path.exists():
        return None
    return Reference.from_dict(read_json(path))


def accept(run: RunRecord, *, now: datetime, source_commit: str, note: str) -> Reference:
    return Reference(
        run=run,
        accepted_at=now.replace(microsecond=0).isoformat(),
        source_commit=source_commit,
        note=note,
    )


def save_reference(root: Path, reference: Reference) -> Path:
    path = reference_path(root, reference.run.candidate)
    write_json(path, reference.to_dict())
    return path
