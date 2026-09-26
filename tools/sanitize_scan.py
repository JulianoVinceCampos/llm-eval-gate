#!/usr/bin/env python3
"""Block organisation context from ever reaching a public commit.

Runs on pre-commit and in CI as the first job. Zero dependencies so it works on a clean
machine with nothing installed but Python.

Why this exists: a leaked hostname or account id in a public git history is permanent;
you cannot un-publish it. So the check runs before anything else, and it fails closed.

Two kinds of rules:

- **Generic shapes**, published here: cloud instance and account ids, Brazilian tax ids,
  private addresses. They say nothing about anyone.
- **Organisation names**, never published. A public denylist of internal domains, hosts
  and product names is itself the leak it is meant to prevent: it tells every reader what
  the internal names are. So those patterns live outside the repository, one regex per
  line, in the `SANITIZE_DENYLIST` environment variable (a CI secret) and in a local
  `.sanitize-denylist` file that git ignores. A finding from that list is reported by file
  and line only; neither the pattern nor the matched text is ever printed.

    python3 tools/sanitize_scan.py            # scan the repository
    python3 tools/sanitize_scan.py path ...   # scan specific paths

Exit 0 clean, 1 findings, 2 usage error.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DENYLIST_ENV = "SANITIZE_DENYLIST"
DENYLIST_FILE = ".sanitize-denylist"
DENYLIST_RULE = "org-denylist"
REDACTED = "<redacted>"

# The rule files legitimately contain the patterns themselves. Nothing else is exempt: the
# tests assemble their leak-shaped fixtures at runtime instead of spelling them out, so a
# real leak added to a test file is still caught. Keep in sync with the `exclude` lists in
# .semgrep/no-corp-leak.yml and the `paths` allowlist in .gitleaks.toml.
SELF_EXEMPT = {
    "tools/sanitize_scan.py",
    ".semgrep/no-corp-leak.yml",
    ".gitleaks.toml",
}

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
# Local artifacts, never committed (see .gitignore). A coverage report is full of long
# digit runs that look like tax ids to a regex, and the private denylist holds the very
# patterns it feeds, so neither is scanned.
SKIP_FILES = {"coverage.json", "coverage.xml", ".coverage", DENYLIST_FILE}
SCAN_SUFFIXES = {
    ".py",
    ".md",
    ".yml",
    ".yaml",
    ".toml",
    ".json",
    ".jsonl",
    ".cfg",
    ".ini",
    ".txt",
    ".sh",
    ".js",
    ".html",
    ".css",
    ".properties",
    ".json5",
    "",
}

# Documented placeholders. Anything here is safe by construction.
ALLOWLIST = (
    "000000000000",
    "i-0EXAMPLE",
    "example.com",
    "users.noreply.github.com",
    "203.0.113.",  # RFC 5737 TEST-NET-3
    "198.51.100.",  # RFC 5737 TEST-NET-2
    "192.0.2.",  # RFC 5737 TEST-NET-1
    "127.0.0.1",
    "0.0.0.0",  # noqa: S104 - an allowlist entry, nothing binds here
)

RULES: tuple[tuple[str, str, str], ...] = (
    ("aws-instance-id", r"\bi-0[a-f0-9]{8,17}\b", "real AWS instance id - use i-0EXAMPLE"),
    ("aws-account-id", r"\b\d{12}\b", "12-digit account id - use 000000000000"),
    (
        "brazilian-tax-id",
        r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b|\b\d{3}\.\d{3}\.\d{3}-\d{2}\b",
        "CNPJ/CPF shaped number - generate a synthetic one",
    ),
    (
        "private-ip",
        r"\b(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}\b",
        "private IP - use the RFC 5737 ranges",
    ),
)
DENYLIST_HINT = "organisation name from the private denylist - describe the pattern, not the name"

COMPILED = tuple((name, re.compile(pattern), hint) for name, pattern, hint in RULES)

Finding = tuple[Path, int, str, str, str]


class DenylistError(ValueError):
    """A line of the private denylist is not a valid regex. The pattern is not echoed."""

    def __init__(self, source: str, line: int) -> None:
        super().__init__(f"{source}, line {line}: not a valid regular expression")


def _denylist_lines(text: str, source: str) -> Iterator[tuple[str, int, str]]:
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line and not line.startswith("#"):
            yield source, number, line


def load_denylist(
    environ: Mapping[str, str] | None = None, root: Path = ROOT
) -> tuple[re.Pattern[str], ...]:
    """Compile the private patterns: the environment variable first, then the local file."""
    env = environ if environ is not None else os.environ
    entries = list(_denylist_lines(env.get(DENYLIST_ENV, ""), DENYLIST_ENV))
    local = root / DENYLIST_FILE
    if local.is_file():
        entries.extend(_denylist_lines(local.read_text(encoding="utf-8"), DENYLIST_FILE))
    patterns: list[re.Pattern[str]] = []
    for source, number, line in entries:
        try:
            patterns.append(re.compile(line, re.IGNORECASE))
        except re.error:
            raise DenylistError(source, number) from None
    return tuple(patterns)


def _iter_files(targets: list[Path]) -> Iterator[Path]:
    for target in targets:
        if target.is_file():
            yield target
            continue
        for path in sorted(target.rglob("*")):
            if not path.is_file() or path.name in SKIP_FILES:
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in SCAN_SUFFIXES:
                yield path


def _allowlisted(line: str) -> bool:
    """A line is waived by a documented placeholder or an explicit `sanitize-ok`."""
    if any(token in line for token in ALLOWLIST):
        return True
    return "sanitize-ok" in line


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def scan(
    targets: list[Path],
    root: Path = ROOT,
    denylist: Sequence[re.Pattern[str]] = (),
) -> list[Finding]:
    findings: list[Finding] = []
    for path in _iter_files(targets):
        # The rule files are exempt from the generic shapes they define, never from the
        # private list: an organisation name has no business in them either.
        generic = _relative(path, root) not in SELF_EXEMPT
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(content.splitlines(), start=1):
            if generic:
                for name, pattern, hint in COMPILED:
                    match = pattern.search(line)
                    if match and not _allowlisted(line):
                        findings.append((path, number, name, match.group(0)[:60], hint))
            # No allowlist and no waiver for the private list: an organisation name is
            # never a placeholder, and `sanitize-ok` next to it would publish it anyway.
            if any(pattern.search(line) for pattern in denylist):
                findings.append((path, number, DENYLIST_RULE, REDACTED, DENYLIST_HINT))
    return findings


def main(argv: list[str], environ: Mapping[str, str] | None = None, root: Path = ROOT) -> int:
    targets = [Path(arg) for arg in argv[1:]] or [root]
    for target in targets:
        if not target.exists():
            print(f"error: path not found: {target}", file=sys.stderr)
            return 2
    try:
        denylist = load_denylist(environ, root)
    except DenylistError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    findings = scan(targets, root, denylist)
    private = f"{len(denylist)} private pattern(s)" if denylist else "no private denylist"
    if not findings:
        print(f"sanitize: clean ({private})")
        return 0

    print(f"sanitize: {len(findings)} finding(s), {private}\n", file=sys.stderr)
    for path, number, name, snippet, hint in findings:
        print(
            f"  {_relative(path, root)}:{number} [{name}] {snippet!r}\n      {hint}",
            file=sys.stderr,
        )
    print(
        "\nIf a generic match is a deliberate placeholder, add it to ALLOWLIST or end the "
        "line with a `sanitize-ok` comment explaining why. Organisation names have no waiver.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
