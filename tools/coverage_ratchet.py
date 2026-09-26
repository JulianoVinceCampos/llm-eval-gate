#!/usr/bin/env python3
"""Coverage ratchet: the floor only moves up.

Reads the JSON report written by `coverage json` (line and branch coverage combined, the
`percent_covered` total) and compares it with the committed `.coverage-floor`.

- below the floor: fail. A pull request that lowers coverage has to say why.
- above it: `--update` raises the floor to the measured value minus a headroom, rounded
  down to one decimal, and the change lands as a reviewed diff. The headroom (0.2 p.p. by
  default) exists because the measurement that counts is the Linux CI runner's, and a
  handful of platform-dependent branches should not turn a laptop-raised floor red.

The JSON report is read instead of the XML one on purpose: parsing XML from a file on
disk is exactly what static analysis flags, and there is no reason to argue with it.

    python3 tools/coverage_ratchet.py coverage.json
    python3 tools/coverage_ratchet.py coverage.json --update

Exit 0 ok, 1 below the floor, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FLOOR_FILE = ROOT / ".coverage-floor"
DEFAULT_HEADROOM = 0.2


def read_total(report: Path) -> float:
    data = json.loads(report.read_text(encoding="utf-8"))
    try:
        return float(data["totals"]["percent_covered"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"{report}: not a coverage JSON report ({error})") from None


def read_floor(path: Path) -> float:
    return float(path.read_text(encoding="utf-8").strip()) if path.exists() else 0.0


def round_down(value: float) -> float:
    return math.floor(value * 10) / 10


def decide(
    total: float, floor: float, *, update: bool, headroom: float = 0.0
) -> tuple[int, float, str]:
    """(exit code, floor to write, message). Pure, for the tests."""
    if total + 1e-9 < floor:
        return 1, floor, f"coverage {total:.2f}% is below the floor {floor:.1f}%"
    new_floor = round_down(total - headroom)
    if update and new_floor > floor:
        return 0, new_floor, f"coverage {total:.2f}%: floor raised {floor:.1f}% -> {new_floor:.1f}%"
    return 0, floor, f"coverage {total:.2f}% holds the floor {floor:.1f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Coverage ratchet: the floor only moves up.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--floor-file", type=Path, default=FLOOR_FILE)
    parser.add_argument("--update", action="store_true")
    parser.add_argument(
        "--headroom",
        type=float,
        default=DEFAULT_HEADROOM,
        help="p.p. kept below the measured total when --update raises the floor",
    )
    args = parser.parse_args(argv)
    if not args.report.exists():
        print(f"error: report not found: {args.report}", file=sys.stderr)
        return 2
    try:
        total = read_total(args.report)
        floor = read_floor(args.floor_file)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    code, new_floor, message = decide(total, floor, update=args.update, headroom=args.headroom)
    print(message, file=sys.stderr if code else sys.stdout)
    if new_floor != floor:
        args.floor_file.write_bytes(f"{new_floor:.1f}\n".encode("ascii"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
