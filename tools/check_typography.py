#!/usr/bin/env python3
"""Refuse em dashes and en dashes anywhere in the repository.

House style, enforced instead of remembered: the em dash is the most recognisable mark
of generated prose, and one of them undermines everything around it. Sentences are
split with a period, a colon or a comma instead. A plain hyphen is fine inside compound
words (`read-only`, `cross-account`).

    python3 tools/check_typography.py          # whole repository
    python3 tools/check_typography.py path ... # specific paths

Exit 0 clean, 1 findings, 2 usage error.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = {"\u2014": "em dash", "\u2013": "en dash"}
SELF_EXEMPT = {"tools/check_typography.py"}
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".hypothesis",
    "node_modules",
    "dist",
    "build",
    "out",
    "htmlcov",
}
SKIP_FILES = {"coverage.json", "coverage.xml", ".coverage"}
SUFFIXES = {
    ".py",
    ".md",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".json5",
    ".jsonl",
    ".js",
    ".html",
    ".css",
    ".txt",
    ".properties",
    ".cfg",
    ".ini",
    ".sh",
    "",
}

Finding = tuple[Path, int, int, str]


def _wanted(path: Path) -> bool:
    return (
        path.is_file()
        and path.name not in SKIP_FILES
        and not any(part in SKIP_DIRS for part in path.parts)
        and path.suffix.lower() in SUFFIXES
    )


def _files(targets: list[Path]) -> Iterator[Path]:
    for target in targets:
        if target.is_file():
            yield target
            continue
        yield from (path for path in sorted(target.rglob("*")) if _wanted(path))


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def scan(targets: list[Path], root: Path = ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for path in _files(targets):
        if _relative(path, root) in SELF_EXEMPT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for column, char in enumerate(line, start=1):
                if char in FORBIDDEN:
                    findings.append((path, number, column, FORBIDDEN[char]))
    return findings


def main(argv: list[str]) -> int:
    targets = [Path(arg) for arg in argv[1:]] or [ROOT]
    missing = [t for t in targets if not t.exists()]
    if missing:
        print(f"error: path not found: {missing[0]}", file=sys.stderr)
        return 2
    findings = scan(targets)
    if not findings:
        print("typography: clean")
        return 0
    for path, number, column, name in findings:
        print(f"  {_relative(path, ROOT)}:{number}:{column} {name}", file=sys.stderr)
    print(f"typography: {len(findings)} finding(s); split the sentence instead", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
