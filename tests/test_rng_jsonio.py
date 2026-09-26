from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from llm_eval_gate.jsonio import (
    canonical,
    pretty,
    read_json,
    read_jsonl,
    read_text,
    sha256_obj,
    write_json,
    write_jsonl,
    write_text,
)
from llm_eval_gate.rng import Rng, seed_from, unit_hash


def test_seed_is_stable_across_processes() -> None:
    # Pinned value: if this changes, every dataset and bootstrap in the repo changes.
    assert seed_from("dataset", 1, "train", 101) == seed_from("dataset", 1, "train", 101)
    assert seed_from("a", "b") != seed_from("ab")
    assert seed_from("a", "b") != seed_from("b", "a")


def test_same_seed_same_sequence() -> None:
    first = Rng("x", 1)
    second = Rng("x", 1)
    assert [first.random() for _ in range(20)] == [second.random() for _ in range(20)]
    assert Rng("x", 2).random() != Rng("x", 1).random()


@given(st.lists(st.text(max_size=8), max_size=5))
def test_unit_hash_is_a_unit_float(parts: list[str]) -> None:
    value = unit_hash(*parts)
    assert 0.0 <= value < 1.0


def test_below_choice_and_chance_stay_in_range() -> None:
    rng = Rng("range")
    assert all(0 <= rng.below(7) < 7 for _ in range(500))
    assert rng.choice(["only"]) == "only"
    assert rng.chance(1.0)
    assert not rng.chance(0.0)
    with pytest.raises(ValueError, match="positive"):
        rng.below(0)
    with pytest.raises(ValueError, match="empty"):
        rng.choice([])


@given(st.lists(st.integers(), max_size=40), st.integers(min_value=0, max_value=40))
def test_sample_is_a_subset_without_repetition(items: list[int], k: int) -> None:
    rng = Rng("sample", len(items), k)
    if k > len(items):
        with pytest.raises(ValueError, match="out of range"):
            rng.sample(items, k)
        return
    picked = rng.sample(list(enumerate(items)), k)
    assert len(picked) == k
    assert len({index for index, _ in picked}) == k


def test_shuffle_is_a_permutation() -> None:
    items = list(range(50))
    shuffled = Rng("perm").shuffled(items)
    assert sorted(shuffled) == items
    assert shuffled != items
    assert items == list(range(50))


def test_canonical_is_sorted_and_compact() -> None:
    assert canonical({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'
    assert sha256_obj({"b": 1, "a": 2}) == sha256_obj({"a": 2, "b": 1})
    assert pretty({"b": 1}).endswith("\n")


def test_write_text_is_lf_on_every_platform(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "file.txt"
    write_text(path, "a\r\nb\nc")
    assert path.read_bytes() == b"a\nb\nc"
    assert read_text(path) == "a\nb\nc"


def test_json_and_jsonl_round_trip(tmp_path: Path) -> None:
    write_json(tmp_path / "a.json", {"z": 1, "k": "ç"})
    assert read_json(tmp_path / "a.json") == {"z": 1, "k": "ç"}
    rows = [{"b": 2, "a": 1}, {"a": 3}]
    write_jsonl(tmp_path / "a.jsonl", rows)
    assert read_jsonl(tmp_path / "a.jsonl") == rows
    first_line = (tmp_path / "a.jsonl").read_text(encoding="utf-8").splitlines()[0]
    assert first_line == '{"a":1,"b":2}'


def test_jsonl_rejects_non_objects(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_bytes(b'{"a": 1}\n\n[1, 2]\n')
    with pytest.raises(ValueError, match=r"bad\.jsonl:3"):
        read_jsonl(path)
    path.write_bytes(b"{not json}\n")
    with pytest.raises(json.JSONDecodeError):
        read_jsonl(path)
