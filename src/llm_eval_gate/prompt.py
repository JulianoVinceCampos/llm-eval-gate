"""Versioned prompt template.

The prompt is an artifact like any other: it has a file, an id and a sha256. The run
record carries the hash, so a pull request that edits one word of the prompt produces a
different fingerprint, misses every recorded answer and cannot pass the gate without new
evidence.

The label catalog is injected from labels.py, never copied into the template, so the
prompt and the parser cannot drift apart.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from llm_eval_gate.jsonio import read_text, sha256_obj
from llm_eval_gate.labels import CATALOG, Label

PLACEHOLDER_CATALOG = "{{label_catalog}}"
PLACEHOLDER_LABELS = "{{label_values}}"
PLACEHOLDER_CASE = "{{case_text}}"
DEFAULT_MAX_CASE_CHARS = 6000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class PromptError(ValueError):
    """The template file is malformed."""


def label_catalog_text() -> str:
    return "\n".join(f"- {item.label.value}: {item.mechanism}" for item in CATALOG)


def label_values_text() -> str:
    return ", ".join(label.value for label in Label)


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    id: str
    system: str
    user: str
    max_case_chars: int = DEFAULT_MAX_CASE_CHARS

    @property
    def rendered_system(self) -> str:
        return self.system.replace(PLACEHOLDER_CATALOG, label_catalog_text()).replace(
            PLACEHOLDER_LABELS, label_values_text()
        )

    @property
    def sha256(self) -> str:
        return sha256_obj(
            {
                "id": self.id,
                "system": self.rendered_system,
                "user": self.user,
                "max_case_chars": self.max_case_chars,
            }
        )

    def render(self, case_text: str) -> tuple[str, str]:
        """(system, user) for one case. The case text is data and is fenced as such."""
        text = _CONTROL.sub("", case_text)
        if len(text) > self.max_case_chars:
            text = text[: self.max_case_chars] + "\n[truncated]"
        return self.rendered_system, self.user.replace(PLACEHOLDER_CASE, text)


def load_prompt(path: Path) -> PromptTemplate:
    try:
        data = tomllib.loads(read_text(path))
    except tomllib.TOMLDecodeError as error:
        raise PromptError(f"{path}: invalid TOML: {error}") from None
    section = data.get("prompt")
    if not isinstance(section, dict):
        raise PromptError(f"{path}: missing [prompt] table")
    missing = [key for key in ("id", "system", "user") if not isinstance(section.get(key), str)]
    if missing:
        raise PromptError(f"{path}: [prompt] needs string keys {missing}")
    user = str(section["user"])
    if user.count(PLACEHOLDER_CASE) != 1:
        raise PromptError(f"{path}: `user` must contain {PLACEHOLDER_CASE} exactly once")
    max_chars = section.get("max_case_chars", DEFAULT_MAX_CASE_CHARS)
    if not isinstance(max_chars, int) or max_chars < 200:
        raise PromptError(f"{path}: `max_case_chars` must be an integer >= 200")
    return PromptTemplate(
        id=str(section["id"]), system=str(section["system"]), user=user, max_case_chars=max_chars
    )
