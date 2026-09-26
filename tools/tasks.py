#!/usr/bin/env python3
"""Task runner with no dependencies: the same steps as the Makefile, on any OS.

    python tools/tasks.py check     # everything the pull request pipeline runs, locally
    python tools/tasks.py test      # tests with coverage and the ratchet
    python tools/tasks.py lint      # ruff and mypy
    python tools/tasks.py gate      # artifact checks and the gate itself
    python tools/tasks.py list      # the steps, in order

Each step is one command. The runner stops at the first failure and prints which step
broke, so the local loop and CI fail on the same thing for the same reason.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
CLI = [PY, "-m", "llm_eval_gate.cli"]

STEPS: dict[str, list[str]] = {
    "sanitize": [PY, "tools/sanitize_scan.py"],
    "typography": [PY, "tools/check_typography.py"],
    "ruff": [PY, "-m", "ruff", "check", "src", "tests", "tools"],
    "format": [PY, "-m", "ruff", "format", "--check", "src", "tests", "tools"],
    "mypy": [PY, "-m", "mypy"],
    "pytest": [PY, "-m", "pytest", "--cov", "--cov-report=json:coverage.json", "--cov-report=term"],
    "ratchet": [PY, "tools/coverage_ratchet.py", "coverage.json"],
    "datasets": [*CLI, "datasets", "--check"],
    "fit": [*CLI, "fit", "--check"],
    "scenarios": [*CLI, "scenarios"],
    "readme": [*CLI, "readme", "--check"],
    "ci": [*CLI, "ci"],
}
GROUPS: dict[str, tuple[str, ...]] = {
    "lint": ("sanitize", "typography", "ruff", "format", "mypy"),
    "test": ("pytest", "ratchet"),
    "gate": ("datasets", "fit", "scenarios", "readme", "ci"),
}
GROUPS["check"] = GROUPS["lint"] + GROUPS["test"] + GROUPS["gate"]


def run(names: Sequence[str]) -> int:
    for name in names:
        command = STEPS[name]
        print(f"==> {name}: {' '.join(command[1:])}", flush=True)
        started = time.perf_counter()
        code = subprocess.call(command, cwd=ROOT)  # noqa: S603 - fixed, local commands
        elapsed = time.perf_counter() - started
        if code != 0:
            print(f"FAILED at `{name}` (exit {code}, {elapsed:.1f}s)", file=sys.stderr)
            return code
        print(f"    ok ({elapsed:.1f}s)", flush=True)
    return 0


def main(argv: Sequence[str]) -> int:
    target = argv[1] if len(argv) > 1 else "check"
    if target == "list":
        for group, names in GROUPS.items():
            print(f"{group}: {', '.join(names)}")
        return 0
    if target in GROUPS:
        return run(GROUPS[target])
    if target in STEPS:
        return run([target])
    print(f"unknown task {target!r}; try `list`", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
