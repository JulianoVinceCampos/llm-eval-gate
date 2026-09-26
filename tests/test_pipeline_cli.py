"""End to end through the CLI, on a copy of the repository."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import keyword_factory, synthetic_config, synthetic_run
from llm_eval_gate import __version__
from llm_eval_gate.cli import main
from llm_eval_gate.factory import PROVIDERS
from llm_eval_gate.jsonio import read_json, write_json
from llm_eval_gate.pipeline import Project, load_workspace, save_run
from llm_eval_gate.reference import Reference, reference_path, save_reference

LLM = "ollama-qwen2.5-0.5b"


def cli(project: Project, *args: str) -> int:
    return main(["--root", str(project.root), *args])


def test_ci_passes_on_the_committed_repository(
    tmp_project: Project,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    report, markdown = tmp_path / "ci.json", tmp_path / "ci.md"
    assert cli(tmp_project, "ci", "--json", str(report), "--markdown", str(markdown)) == 0
    data = read_json(report)
    assert data["exit_code"] == 0
    outcomes = {c["name"]: c["outcome"] for c in data["candidates"]}
    assert outcomes == {LLM: "pending", "rules-signature": "pass", "synthetic-calibrated": "pass"}
    assert "## llm-eval-gate: PASSA" in markdown.read_text(encoding="utf-8")
    assert "## llm-eval-gate: PASSA" in summary.read_text(encoding="utf-8")
    assert "PENDENTE" in capsys.readouterr().out


def test_tampered_dataset_stops_the_pipeline(
    tmp_project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli(tmp_project, "datasets", "--check") == 0
    path = tmp_project.root / "datasets" / "in-dist.jsonl"
    path.write_bytes(path.read_bytes().replace(b"svc-orders", b"svc-ordrs", 1))
    assert cli(tmp_project, "datasets", "--check") == 1
    assert "in-dist.jsonl" in capsys.readouterr().err
    assert cli(tmp_project, "ci") == 1
    assert "fora de sincronia com o gerador" in capsys.readouterr().out
    assert cli(tmp_project, "datasets") == 0
    assert cli(tmp_project, "datasets", "--check") == 0


def test_tampered_baseline_is_caught(tmp_project: Project) -> None:
    assert cli(tmp_project, "fit", "--check") == 0
    model = read_json(tmp_project.model_path)
    model["threshold"] = 0.99
    write_json(tmp_project.model_path, model)
    assert cli(tmp_project, "fit", "--check") == 1
    tmp_project.model_path.unlink()
    assert cli(tmp_project, "fit", "--check") == 1
    assert cli(tmp_project, "fit") == 0
    assert cli(tmp_project, "fit", "--check") == 0


def test_readme_block_is_regenerated_and_checked(tmp_project: Project) -> None:
    assert cli(tmp_project, "readme", "--check") == 0
    readme = tmp_project.readme_path
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("47.3%", "57.3%"), encoding="utf-8"
    )
    assert cli(tmp_project, "readme", "--check") == 1
    assert cli(tmp_project, "readme") == 0
    assert cli(tmp_project, "readme", "--check") == 0
    readme.write_text("# no markers\n", encoding="utf-8")
    assert cli(tmp_project, "readme") == 1


def test_gate_json_and_blocking(tmp_project: Project, tmp_path: Path) -> None:
    out = tmp_path / "gate.json"
    assert (
        cli(
            tmp_project,
            "gate",
            "synthetic-calibrated",
            "--json",
            str(out),
            "--summary",
            str(tmp_path / "s.md"),
        )
        == 0
    )
    assert read_json(out)["outcome"] == "pass"
    # A production candidate without an accepted reference cannot pass.
    reference_path(tmp_project.root, "rules-signature").unlink()
    assert cli(tmp_project, "gate", "rules-signature") == 1
    # An experimental one can: it is its first evaluation.
    reference_path(tmp_project.root, "synthetic-calibrated").unlink()
    assert cli(tmp_project, "gate", "synthetic-calibrated") == 0


def test_deleting_the_cassette_cannot_pass_the_gate(tmp_project: Project, tmp_path: Path) -> None:
    # Evidence was accepted once, then the cassette went missing: that is a failure, or
    # deleting the cassette would be the cheapest way past the gate.
    ws = load_workspace(tmp_project)
    run = synthetic_run(ws.cases[:20], synthetic_config(name=LLM), ws.dataset_sha256)
    save_reference(
        tmp_project.root, Reference(run, "2026-09-26T00:00:00+00:00", "abc", "evidencia antiga")
    )
    report = tmp_path / "ci.json"
    assert cli(tmp_project, "ci", "--json", str(report)) == 1
    llm = next(c for c in read_json(report)["candidates"] if c["name"] == LLM)
    assert (llm["status"], llm["outcome"], llm["blocking"]) == ("pending", "fail", True)


def test_llm_path_offline_record_accept_replay_compare(
    tmp_project: Project, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(PROVIDERS, "ollama", keyword_factory)
    assert cli(tmp_project, "run", LLM, "--mode", "record") == 0
    cassette = tmp_project.root / "evals" / "cassettes" / f"{LLM}.jsonl"
    # 250 sampled cases x 3 repeats; identical texts would share an entry, none do here.
    assert len(cassette.read_text(encoding="utf-8").splitlines()) == 750
    saved = tmp_project.runs_dir / f"{LLM}.json"
    assert saved.exists()
    assert cli(tmp_project, "accept", LLM, "--run", str(saved), "--note", "primeira gravacao") == 0
    reference = read_json(reference_path(tmp_project.root, LLM))
    assert reference["note"] == "primeira gravacao"
    capsys.readouterr()
    assert cli(tmp_project, "gate", LLM) == 0
    assert "PASSA" in capsys.readouterr().out
    assert cli(tmp_project, "compare", "rules-signature", LLM, "--split", "hard") == 0
    assert "McNemar exato" in capsys.readouterr().out
    assert cli(tmp_project, "readme") == 0
    assert "Comparação pareada" in tmp_project.readme_path.read_text(encoding="utf-8")
    # Changing the prompt by one character misses every recorded answer.
    prompt = tmp_project.root / "prompts" / "classify-v1.toml"
    prompt.write_text(
        prompt.read_text(encoding="utf-8").replace("exactly one", "exactly  one"), encoding="utf-8"
    )
    assert cli(tmp_project, "gate", LLM) == 1
    assert "Re-record" in capsys.readouterr().out


def test_accept_refuses_foreign_runs(tmp_project: Project, tmp_path: Path) -> None:
    ws = load_workspace(tmp_project)
    run = synthetic_run(ws.cases, synthetic_config(name="synthetic-calibrated"), "old-dataset")
    path = tmp_path / "run.json"
    write_json(path, run.to_dict())
    assert cli(tmp_project, "accept", "synthetic-calibrated", "--run", str(path)) == 2
    assert cli(tmp_project, "accept", "rules-signature", "--run", str(path)) == 2


def test_accept_records_the_commit(tmp_project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    git = tmp_project.root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git / "refs" / "heads" / "main").write_text("f" * 40 + "\n", encoding="utf-8")
    assert cli(tmp_project, "accept", "synthetic-calibrated") == 0
    assert (
        read_json(reference_path(tmp_project.root, "synthetic-calibrated"))["source_commit"]
        == "f" * 40
    )
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    assert cli(tmp_project, "accept", "synthetic-calibrated") == 0
    assert (
        read_json(reference_path(tmp_project.root, "synthetic-calibrated"))["source_commit"]
        == "a" * 40
    )


def test_run_writes_where_asked(tmp_project: Project, tmp_path: Path) -> None:
    out = tmp_path / "synthetic.json"
    assert cli(tmp_project, "run", "synthetic-calibrated", "--out", str(out)) == 0
    assert read_json(out)["candidate"] == "synthetic-calibrated"
    ws = load_workspace(tmp_project)
    run = synthetic_run(ws.cases, synthetic_config(), ws.dataset_sha256)
    assert save_run(tmp_project, run).name == "synthetic-test.json"
    # No cassette and no reference yet: pending, reported, not a failure.
    assert cli(tmp_project, "run", LLM) == 0


def test_compare_needs_paired_cases(
    tmp_project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli(tmp_project, "compare", "rules-signature", LLM) == 2
    assert "sem casos pareados" in capsys.readouterr().err
    assert cli(tmp_project, "compare", "rules-signature", "synthetic-calibrated") == 0


@pytest.mark.slow
def test_calibration_round_trip(tmp_project: Project) -> None:
    assert cli(tmp_project, "calibrate", "--trials", "2") == 0
    assert cli(tmp_project, "calibrate", "--check") == 0
    data = read_json(tmp_project.calibration_path)
    data["cells"][0]["pass"] += 1
    write_json(tmp_project.calibration_path, data)
    assert cli(tmp_project, "calibrate", "--check") == 1
    tmp_project.calibration_path.unlink()
    assert cli(tmp_project, "calibrate", "--check") == 1
    assert cli(tmp_project, "readme", "--check") == 1


def test_small_commands(tmp_project: Project, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli(tmp_project, "scenarios") == 0
    assert cli(tmp_project, "labels") == 0
    output = capsys.readouterr().out
    assert "[ok ] regressao-real" in output
    assert "slow-query" in output
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_configuration_errors_exit_with_two(tmp_project: Project, tmp_path: Path) -> None:
    assert main(["--root", str(tmp_path / "missing"), "ci"]) == 2
    assert cli(tmp_project, "gate", "no-such-candidate") == 2
    path = reference_path(tmp_project.root, "synthetic-calibrated")
    path.write_text("{broken", encoding="utf-8")
    assert cli(tmp_project, "gate", "synthetic-calibrated") == 2
    data = {"schema": 99}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert cli(tmp_project, "gate", "synthetic-calibrated") == 2
    policy = tmp_project.policy_path
    policy.write_text("[regression]\nalpha = 2\nmargin = 0.03\n", encoding="utf-8")
    assert cli(tmp_project, "ci") == 2
