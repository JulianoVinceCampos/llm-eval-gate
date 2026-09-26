from __future__ import annotations

import json

from hypothesis import given
from hypothesis import strategies as st

from llm_eval_gate.labels import (
    CATALOG,
    INVALID,
    Label,
    all_labels,
    family_labels,
    info,
    parse_label,
    prediction_values,
)
from llm_eval_gate.parsing import (
    MAX_EVIDENCE_CHARS,
    MAX_OUTPUT_CHARS,
    first_json_object,
    output_schema,
    parse_output,
)


def test_catalog_covers_every_label_once() -> None:
    assert [item.label for item in CATALOG] == list(Label)
    assert len(family_labels()) == 8
    assert Label.NONE not in family_labels()
    assert all_labels()[-1] is Label.NONE
    assert prediction_values()[-1] == INVALID
    assert INVALID not in {label.value for label in Label}
    assert info(Label.CERT).title.startswith("Certificado")


def test_parse_label_forgives_case_space_and_underscore_only() -> None:
    assert parse_label("  POOL_LOCK ") is Label.POOL_LOCK
    assert parse_label("Heap-OOM") is Label.HEAP_OOM
    assert parse_label("pool lock") is None
    assert parse_label("a pool problem") is None
    assert parse_label("") is None


def test_schema_lists_exactly_the_labels() -> None:
    schema = output_schema()
    assert schema["properties"]["label"]["enum"] == [label.value for label in Label]
    assert schema["required"] == ["label"]
    assert schema["additionalProperties"] is False


def test_parse_valid_json() -> None:
    prediction = parse_output('{"label": "cert", "evidence": "expired\\nat the edge"}')
    assert prediction.label is Label.CERT
    assert prediction.evidence == "expired at the edge"
    assert prediction.format_ok
    assert prediction.value == "cert"


def test_parse_json_inside_prose_and_fences() -> None:
    text = (
        'Sure! Here is the answer:\n```json\n{"label": "acl", "evidence": "a {brace} in text"}\n```'
    )
    assert parse_output(text).label is Label.ACL


def test_parse_takes_the_first_object_only() -> None:
    assert parse_output('{"label": "cert"} {"label": "acl"}').label is Label.CERT


def test_parse_rejects_everything_else() -> None:
    for text in (
        "",
        "pool-lock",
        '{"label": "pool problem"}',
        '{"label": 3}',
        '{"evidence": "no label"}',
        '["cert"]',
        '{"label": "cert"',
        "{" * 5000,
    ):
        prediction = parse_output(text)
        assert prediction.label is None, text
        assert prediction.value == INVALID
        assert not prediction.format_ok


def test_evidence_is_bounded_and_cleaned() -> None:
    long_evidence = "x\x07" * 1000
    prediction = parse_output(json.dumps({"label": "none", "evidence": long_evidence}))
    assert prediction.label is Label.NONE
    assert len(prediction.evidence) <= MAX_EVIDENCE_CHARS
    assert "\x07" not in prediction.evidence
    assert parse_output('{"label": "none", "evidence": 42}').evidence == ""


def test_output_is_clipped_before_parsing() -> None:
    padded = " " * MAX_OUTPUT_CHARS + '{"label": "cert"}'
    assert parse_output(padded).label is None


def test_first_json_object_honours_strings() -> None:
    assert first_json_object('x {"a": "}"} y') == '{"a": "}"}'
    assert first_json_object('{"a": "\\"}"}') == '{"a": "\\"}"}'
    assert first_json_object("no object") is None
    assert first_json_object("} {") is None


@given(st.text(max_size=400))
def test_parser_never_raises_and_never_invents(text: str) -> None:
    prediction = parse_output(text)
    if prediction.label is not None:
        # Anything accepted must carry the label value somewhere in the text.
        assert prediction.label.value in text.lower().replace("_", "-")


@given(st.sampled_from(list(Label)), st.text(max_size=60))
def test_any_label_round_trips(label: Label, evidence: str) -> None:
    prediction = parse_output(json.dumps({"label": label.value, "evidence": evidence}))
    assert prediction.label is label
