"""Strict parser for model output.

The model's text is untrusted input. The parser accepts one thing, a JSON object whose
`label` is one of the declared labels, and turns everything else into `invalid`. It never
guesses: mapping "a pool problem" to `pool-lock` would inflate the model score with the
parser's opinion, and the gate would be measuring the wrong component.
"""

from __future__ import annotations

import json
import re
from typing import Any

from llm_eval_gate.domain import Prediction
from llm_eval_gate.labels import Label, parse_label

MAX_OUTPUT_CHARS = 8192
MAX_EVIDENCE_CHARS = 240
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def output_schema() -> dict[str, Any]:
    """JSON schema handed to providers that support structured output."""
    return {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": [label.value for label in Label]},
            "evidence": {"type": "string", "maxLength": MAX_EVIDENCE_CHARS},
        },
        "required": ["label"],
        "additionalProperties": False,
    }


def _loads(text: str) -> object | None:
    try:
        value: object = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value


def first_json_object(text: str) -> str | None:
    """The first balanced {...} in the text, honouring strings and escapes."""
    depth = 0
    start = -1
    in_string = False
    escape = False
    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"' and depth > 0:
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _clean(evidence: str) -> str:
    return _CONTROL.sub(" ", evidence).strip()[:MAX_EVIDENCE_CHARS]


def parse_output(text: str) -> Prediction:
    clipped = text[:MAX_OUTPUT_CHARS]
    value = _loads(clipped.strip())
    if not isinstance(value, dict):
        fragment = first_json_object(clipped)
        value = _loads(fragment) if fragment is not None else None
    if not isinstance(value, dict):
        return Prediction(None)
    raw_label = value.get("label")
    if not isinstance(raw_label, str):
        return Prediction(None)
    label = parse_label(raw_label)
    if label is None:
        return Prediction(None)
    evidence = value.get("evidence", "")
    return Prediction(label, _clean(evidence) if isinstance(evidence, str) else "")
