from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from llm_eval_gate.datasets.generate import DEFAULT_SPECS, SplitSpec, generate_all, generate_split
from llm_eval_gate.datasets.store import (
    build_manifest,
    load_manifest,
    load_split,
    verify,
    write_all,
)
from llm_eval_gate.domain import ALL_SPLITS, SPLIT_HARD, SPLIT_IN, SPLIT_TRAIN
from llm_eval_gate.labels import Label

ID = re.compile(r"^c[0-9a-f]{12}$")
HYPHENATED = [label.value for label in Label if "-" in label.value]


def test_committed_datasets_are_what_the_generator_produces(root: Path) -> None:
    assert verify(root) == []


def test_generation_is_deterministic() -> None:
    assert generate_all() == generate_all()


def test_split_sizes_and_balance(root: Path) -> None:
    manifest = load_manifest(root)
    expected = {SPLIT_TRAIN: (12, 24), SPLIT_IN: (20, 40), SPLIT_HARD: (30, 60)}
    for split, (per_family, none_count) in expected.items():
        cases = load_split(root, split)
        counts = Counter(case.label for case in cases)
        assert counts[Label.NONE] == none_count
        assert all(counts[label] == per_family for label in Label if label is not Label.NONE)
        assert manifest["splits"][split]["count"] == len(cases)
        assert all(case.split == split for case in cases)


def test_ids_are_opaque_and_unique_across_splits(root: Path) -> None:
    ids = [case.id for split in ALL_SPLITS for case in load_split(root, split)]
    assert len(ids) == len(set(ids))
    assert all(ID.match(case_id) for case_id in ids)


def test_texts_do_not_leak_the_label(root: Path) -> None:
    for split in ALL_SPLITS:
        for case in load_split(root, split):
            lowered = case.text.lower()
            assert not any(line.startswith("id:") for line in lowered.splitlines())
            assert not any(value in lowered for value in HYPHENATED), case.id


def test_no_text_is_shared_between_train_and_evaluation(root: Path) -> None:
    train = {case.text for case in load_split(root, SPLIT_TRAIN)}
    for split in (SPLIT_IN, SPLIT_HARD):
        assert not train & {case.text for case in load_split(root, split)}


def test_hard_split_carries_its_perturbations(root: Path) -> None:
    hard = load_split(root, SPLIT_HARD)
    assert all("opaque-title" in case.operators for case in hard)
    assert all(
        ("vocab-shift" in case.operators) != ("vocab-mix" in case.operators) for case in hard
    )
    seen = Counter(op.split(":")[0] for case in hard for op in case.operators)
    for operator in ("noise", "distractor", "free-form", "typo", "code-switch", "omission"):
        assert seen[operator] > 0, operator
    assert {case.lang for case in hard} == {"en", "pt", "mixed"}
    for split in (SPLIT_TRAIN, SPLIT_IN):
        assert all(case.operators == () for case in load_split(root, split))


def test_distractors_never_name_the_true_family(root: Path) -> None:
    for case in load_split(root, SPLIT_HARD):
        for op in case.operators:
            if op.startswith("distractor:"):
                assert op.split(":", 1)[1] != case.label.value


def test_write_and_verify_round_trip(tmp_path: Path) -> None:
    specs = (
        SplitSpec(SPLIT_TRAIN, per_family=1, none_count=1, seed=1, hard=False),
        SplitSpec(SPLIT_IN, per_family=1, none_count=1, seed=2, hard=False),
        SplitSpec(SPLIT_HARD, per_family=1, none_count=2, seed=3, hard=True),
    )
    splits = {spec.name: generate_split(spec) for spec in specs}
    manifest = write_all(tmp_path, splits, specs)
    assert manifest == build_manifest(splits, specs)
    assert verify(tmp_path, specs) == []
    hard = tmp_path / "datasets" / "hard.jsonl"
    hard.write_bytes(hard.read_bytes().replace(b"\n", b"\r\n"))
    assert any("hard.jsonl" in problem for problem in verify(tmp_path, specs))
    (tmp_path / "datasets" / "manifest.json").unlink()
    hard.unlink()
    problems = verify(tmp_path, specs)
    assert any("missing" in problem for problem in problems)
    assert len(problems) == 2


def test_default_specs_are_the_documented_ones() -> None:
    assert [(s.name, s.seed, s.hard) for s in DEFAULT_SPECS] == [
        (SPLIT_TRAIN, 101, False),
        (SPLIT_IN, 202, False),
        (SPLIT_HARD, 303, True),
    ]
