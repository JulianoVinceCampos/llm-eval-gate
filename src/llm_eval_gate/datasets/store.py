"""Dataset files on disk: one JSONL per split plus a manifest with the hashes.

The committed files are evidence, so CI regenerates them and fails if a single byte
differs. That is what makes "the dataset" a reproducible thing and not a folder someone
edited by hand.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from llm_eval_gate.datasets.generate import (
    DEFAULT_SPECS,
    GENERATOR_VERSION,
    SplitSpec,
    generate_all,
)
from llm_eval_gate.domain import EVAL_SPLITS, Case
from llm_eval_gate.jsonio import (
    jsonl_text,
    read_json,
    read_jsonl,
    sha256_bytes,
    sha256_text,
    write_json,
    write_text,
)
from llm_eval_gate.labels import Label

MANIFEST_NAME = "manifest.json"


def datasets_dir(root: Path) -> Path:
    return root / "datasets"


def split_path(root: Path, split: str) -> Path:
    return datasets_dir(root) / f"{split}.jsonl"


def manifest_path(root: Path) -> Path:
    return datasets_dir(root) / MANIFEST_NAME


def serialize(cases: Sequence[Case]) -> str:
    return jsonl_text(case.to_dict() for case in cases)


def build_manifest(
    splits: Mapping[str, Sequence[Case]], specs: tuple[SplitSpec, ...] = DEFAULT_SPECS
) -> dict[str, Any]:
    entries: dict[str, Any] = {}
    for spec in specs:
        cases = splits[spec.name]
        entries[spec.name] = {
            "seed": spec.seed,
            "hard": spec.hard,
            "count": len(cases),
            "labels": {label.value: sum(1 for c in cases if c.label is label) for label in Label},
            "sha256": sha256_text(serialize(cases)),
        }
    eval_material = "|".join(f"{name}:{entries[name]['sha256']}" for name in EVAL_SPLITS)
    return {
        "generator_version": GENERATOR_VERSION,
        "splits": entries,
        "eval_sha256": sha256_text(eval_material),
    }


def write_all(
    root: Path,
    splits: Mapping[str, Sequence[Case]],
    specs: tuple[SplitSpec, ...] = DEFAULT_SPECS,
) -> dict[str, Any]:
    for spec in specs:
        write_text(split_path(root, spec.name), serialize(splits[spec.name]))
    manifest = build_manifest(splits, specs)
    write_json(manifest_path(root), manifest)
    return manifest


def load_manifest(root: Path) -> dict[str, Any]:
    data = read_json(manifest_path(root))
    if not isinstance(data, dict):
        raise ValueError("datasets/manifest.json must be a JSON object")
    return data


def load_split(root: Path, split: str) -> list[Case]:
    return [Case.from_dict(row) for row in read_jsonl(split_path(root, split))]


def load_eval_cases(root: Path) -> list[Case]:
    cases: list[Case] = []
    for split in EVAL_SPLITS:
        cases.extend(load_split(root, split))
    return cases


def eval_sha256(root: Path) -> str:
    return str(load_manifest(root)["eval_sha256"])


def verify(root: Path, specs: tuple[SplitSpec, ...] = DEFAULT_SPECS) -> list[str]:
    """Regenerate every split and compare with the committed bytes. Empty list = in sync."""
    regenerated = generate_all(specs)
    expected = build_manifest(regenerated, specs)
    problems: list[str] = []
    for spec in specs:
        path = split_path(root, spec.name)
        if not path.exists():
            problems.append(f"{path.name}: missing, run `llm-eval-gate datasets`")
            continue
        if sha256_bytes(path.read_bytes()) != expected["splits"][spec.name]["sha256"]:
            problems.append(f"{path.name}: committed bytes differ from the generator output")
    if not manifest_path(root).exists():
        problems.append(f"{MANIFEST_NAME}: missing")
    elif load_manifest(root) != expected:
        problems.append(f"{MANIFEST_NAME}: out of date")
    return problems
