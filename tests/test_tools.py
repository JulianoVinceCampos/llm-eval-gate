from __future__ import annotations

import json
from pathlib import Path

import pytest

import check_typography
import coverage_ratchet
import sanitize_scan
import tasks


def _fixture(*parts: str) -> str:
    """A leak-shaped string assembled at runtime.

    The whole literal never appears in this file, so the file needs no exemption from the
    scanners it tests. An exempted file is exactly where a real leak could hide unseen.
    """
    return "".join(parts)


def test_the_repository_is_clean(root: Path) -> None:
    assert sanitize_scan.scan([root], root) == []
    assert check_typography.scan([root], root) == []


def test_only_the_rule_files_are_exempt() -> None:
    expected = {"tools/sanitize_scan.py", ".semgrep/no-corp-leak.yml", ".gitleaks.toml"}
    assert expected == sanitize_scan.SELF_EXEMPT


def test_no_organisation_name_is_published_in_the_rules() -> None:
    # The public rules are generic shapes only. Internal names live in the private
    # denylist (CI secret or a git-ignored local file), because publishing them in a rule
    # is the very leak the rule exists to prevent.
    assert [name for name, _, _ in sanitize_scan.RULES] == [
        "aws-instance-id",
        "aws-account-id",
        "brazilian-tax-id",
        "private-ip",
    ]


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        (_fixture("host i-0", "abcdef1234567890 went down"), "aws-instance-id"),
        (_fixture("account 123456", "789012 owns it"), "aws-account-id"),
        (_fixture("cnpj 12.345.", "678/0001-90"), "brazilian-tax-id"),
        (_fixture("db at 10.20.", "30.40"), "private-ip"),
    ],
)
def test_sanitize_catches_each_rule(tmp_path: Path, line: str, rule: str) -> None:
    path = tmp_path / "leak.md"
    path.write_text(line + "\n", encoding="utf-8")
    findings = sanitize_scan.scan([path], tmp_path)
    assert [f[2] for f in findings] == [rule]
    assert sanitize_scan.main(["x", str(path)], environ={}, root=tmp_path) == 1


def test_sanitize_allowlist_and_waiver(tmp_path: Path) -> None:
    path = tmp_path / "ok.md"
    lines = [
        "ip 203.0.113.7",
        "account 000000000000",
        _fixture("id 123456", "789012 sanitize-ok: fixture"),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert sanitize_scan.scan([path], tmp_path) == []
    assert sanitize_scan.main(["x", str(path)], environ={}, root=tmp_path) == 0
    assert sanitize_scan.main(["x", str(tmp_path / "missing")], environ={}, root=tmp_path) == 2


# A fictitious organisation name, standing in for the real ones that never enter the repo.
FICTITIOUS = "acme-intranet"


def test_private_denylist_blocks_without_echo_or_waiver(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    leak = tmp_path / "notes.md"
    # `sanitize-ok` waives generic shapes, never an organisation name.
    leak.write_text(f"deploy went to db.{FICTITIOUS}.test sanitize-ok\n", encoding="utf-8")
    env = {sanitize_scan.DENYLIST_ENV: f"# one regex per line\n\n{FICTITIOUS}\n"}
    assert sanitize_scan.main(["x", str(leak)], environ=env, root=tmp_path) == 1
    err = capsys.readouterr().err
    assert "[org-denylist] '<redacted>'" in err
    # Neither the pattern nor the matched text reaches the output, which in CI is public.
    assert FICTITIOUS not in err


def test_private_denylist_from_the_local_file(tmp_path: Path) -> None:
    (tmp_path / sanitize_scan.DENYLIST_FILE).write_text("ACME-Intranet\n", encoding="utf-8")
    patterns = sanitize_scan.load_denylist({}, root=tmp_path)
    assert len(patterns) == 1
    assert patterns[0].search(f"host.{FICTITIOUS}.test")  # case-insensitive
    (tmp_path / "doc.md").write_text(f"see {FICTITIOUS}\n", encoding="utf-8")
    findings = sanitize_scan.scan([tmp_path], tmp_path, denylist=patterns)
    # The denylist file itself is never scanned: it holds the very patterns it feeds.
    assert [(f[0].name, f[2]) for f in findings] == [("doc.md", "org-denylist")]


def test_rule_files_are_not_exempt_from_the_private_list(tmp_path: Path) -> None:
    rules = tmp_path / "tools" / "sanitize_scan.py"
    rules.parent.mkdir()
    rules.write_text(f"{FICTITIOUS}\n{_fixture('123456', '789012')}\n", encoding="utf-8")
    patterns = sanitize_scan.load_denylist({sanitize_scan.DENYLIST_ENV: FICTITIOUS}, tmp_path)
    findings = sanitize_scan.scan([rules], tmp_path, denylist=patterns)
    # Exempt from the generic shapes it defines (line 2), not from the private list.
    assert [(f[1], f[2]) for f in findings] == [(1, "org-denylist")]


def test_invalid_private_pattern_is_a_usage_error_without_echo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clean = tmp_path / "clean.md"
    clean.write_text("nada aqui\n", encoding="utf-8")
    env = {sanitize_scan.DENYLIST_ENV: "valid\n(unclosed"}
    assert sanitize_scan.main(["x", str(clean)], environ=env, root=tmp_path) == 2
    err = capsys.readouterr().err
    assert "line 2" in err
    assert "(unclosed" not in err
    assert sanitize_scan.main(["x", str(clean)], environ={}, root=tmp_path) == 0
    assert "no private denylist" in capsys.readouterr().out


def test_typography_flags_both_dashes(tmp_path: Path) -> None:
    path = tmp_path / "prose.md"
    path.write_text(
        "uma frase \u2014 outra\nintervalo 1\u20132\nread-only e ok\n", encoding="utf-8"
    )
    findings = check_typography.scan([path], tmp_path)
    assert [(f[1], f[3]) for f in findings] == [(1, "em dash"), (2, "en dash")]
    assert check_typography.main(["x", str(path)]) == 1
    assert check_typography.main(["x", str(tmp_path / "nope")]) == 2
    clean = tmp_path / "clean.md"
    clean.write_text("tudo certo.\n", encoding="utf-8")
    assert check_typography.main(["x", str(clean)]) == 0


def test_ratchet_only_moves_up(tmp_path: Path) -> None:
    assert coverage_ratchet.decide(91.27, 90.0, update=False) == (
        0,
        90.0,
        "coverage 91.27% holds the floor 90.0%",
    )
    assert coverage_ratchet.decide(91.27, 90.0, update=True)[1] == 91.2
    assert coverage_ratchet.decide(91.27, 90.0, update=True, headroom=0.2)[1] == 91.0
    # Headroom never lowers the floor: 90.1 - 0.2 rounds below 90.0, so it stays.
    assert coverage_ratchet.decide(90.1, 90.0, update=True, headroom=0.2)[1] == 90.0
    assert coverage_ratchet.decide(89.9, 90.0, update=True)[0] == 1
    assert coverage_ratchet.round_down(91.99) == 91.9

    report = tmp_path / "coverage.json"
    report.write_text(json.dumps({"totals": {"percent_covered": 93.456}}), encoding="utf-8")
    floor = tmp_path / ".coverage-floor"
    floor.write_text("90.0\n", encoding="utf-8")
    assert coverage_ratchet.main([str(report), "--floor-file", str(floor)]) == 0
    assert floor.read_text(encoding="utf-8") == "90.0\n"
    assert coverage_ratchet.main([str(report), "--floor-file", str(floor), "--update"]) == 0
    # Default headroom of 0.2 p.p.: 93.456 - 0.2 = 93.256, rounded down.
    assert floor.read_text(encoding="utf-8") == "93.2\n"
    args = [str(report), "--floor-file", str(floor), "--update", "--headroom", "0"]
    assert coverage_ratchet.main(args) == 0
    assert floor.read_text(encoding="utf-8") == "93.4\n"
    report.write_text(json.dumps({"totals": {"percent_covered": 80.0}}), encoding="utf-8")
    assert coverage_ratchet.main([str(report), "--floor-file", str(floor)]) == 1
    report.write_text("{}", encoding="utf-8")
    assert coverage_ratchet.main([str(report), "--floor-file", str(floor)]) == 2
    assert coverage_ratchet.main([str(tmp_path / "none.json")]) == 2


def test_committed_floor_is_a_number(root: Path) -> None:
    assert 0 < float((root / ".coverage-floor").read_text(encoding="utf-8")) <= 100


def test_task_runner_knows_its_steps(capsys: pytest.CaptureFixture[str]) -> None:
    assert tasks.main(["tasks", "list"]) == 0
    assert "check:" in capsys.readouterr().out
    assert tasks.main(["tasks", "nope"]) == 2
    assert set(tasks.GROUPS["check"]) == set(tasks.STEPS)
