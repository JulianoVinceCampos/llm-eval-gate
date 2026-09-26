"""Canonical JSON and byte-exact file IO.

Every artifact the gate compares is hashed, so its bytes have to be the same on every
operating system. Two traps are closed here:

- `Path.write_text` translates "\\n" to the platform line separator. On Windows that turns
  every artifact into CRLF and every hash into a different number. Writes go through
  bytes, always LF.
- `json.dumps` without `sort_keys` depends on insertion order, which is an accident of the
  code that built the dict. Canonical output sorts keys and fixes separators.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


class SchemaError(ValueError):
    """A committed artifact was written by another version of its schema."""


def canonical(obj: object) -> str:
    """Compact, sorted, stable. The form that gets hashed."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def pretty(obj: object) -> str:
    """Sorted and indented, with a trailing newline. The form that gets committed."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_obj(obj: object) -> str:
    return sha256_text(canonical(obj))


def write_text(path: Path, text: str) -> None:
    """Write UTF-8 with LF line endings, whatever the platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def write_json(path: Path, obj: object) -> None:
    write_text(path, pretty(obj))


def jsonl_text(rows: Iterable[Mapping[str, Any]]) -> str:
    return "".join(canonical(dict(row)) + "\n" for row in rows)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    write_text(path, jsonl_text(rows))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(read_text(path).splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{number}: expected a JSON object per line")
        rows.append(value)
    return rows
