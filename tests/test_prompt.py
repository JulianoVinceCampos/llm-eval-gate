from __future__ import annotations

from pathlib import Path

import pytest

from llm_eval_gate.labels import Label
from llm_eval_gate.prompt import (
    PLACEHOLDER_CASE,
    PromptError,
    PromptTemplate,
    label_catalog_text,
    load_prompt,
)


def test_committed_prompt_renders_the_catalog(root: Path) -> None:
    prompt = load_prompt(root / "prompts" / "classify-v1.toml")
    system, user = prompt.render("Pool 80/80 on every node.")
    assert "{{" not in system
    for label in Label:
        assert f"- {label.value}:" in system
    assert "Pool 80/80 on every node." in user
    assert user.count("POSTMORTEM") == 2


def test_hash_changes_with_any_word() -> None:
    base = PromptTemplate("p", "sys {{label_catalog}}", f"u {PLACEHOLDER_CASE}")
    edited = PromptTemplate("p", "sys  {{label_catalog}}", f"u {PLACEHOLDER_CASE}")
    assert base.sha256 != edited.sha256
    assert (
        base.sha256 == PromptTemplate("p", "sys {{label_catalog}}", f"u {PLACEHOLDER_CASE}").sha256
    )


def test_case_text_is_sanitised_and_truncated() -> None:
    prompt = PromptTemplate("p", "s", f"<{PLACEHOLDER_CASE}>", max_case_chars=200)
    _, user = prompt.render("a\x00b\x1bc" + "x" * 500)
    assert "\x00" not in user
    assert "\x1b" not in user
    assert user.endswith("[truncated]>")
    assert "abc" in user


def test_catalog_text_has_one_line_per_label() -> None:
    assert len(label_catalog_text().splitlines()) == len(Label)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("not toml [", "invalid TOML"),
        ("[other]\nx = 1\n", "missing \\[prompt\\]"),
        ('[prompt]\nid = "p"\nsystem = "s"\n', "needs string keys"),
        ('[prompt]\nid = "p"\nsystem = "s"\nuser = "no placeholder"\n', "exactly once"),
        (
            '[prompt]\nid = "p"\nsystem = "s"\nuser = "{{case_text}}"\nmax_case_chars = 10\n',
            "max_case_chars",
        ),
    ],
)
def test_malformed_prompts_are_rejected(tmp_path: Path, body: str, message: str) -> None:
    path = tmp_path / "p.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(PromptError, match=message):
        load_prompt(path)
