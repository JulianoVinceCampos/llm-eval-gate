"""Shared fixtures.

The committed artifacts (datasets, fitted baseline, references) are part of what the
suite verifies, so most tests read the repository itself. Tests that write get a copy in
a temporary directory, never the working tree.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from hypothesis import settings

from llm_eval_gate.pipeline import Project, Workspace, load_workspace

ROOT = Path(__file__).resolve().parents[1]
COPIED = ("datasets", "evals", "spec", "prompts", "README.md")

# In CI the property tests run derandomized: the same examples on every run. The coverage
# ratchet is measured there, and a floor that moves because Hypothesis happened to explore
# a new branch today is noise, not signal. Locally they stay random, which is where a new
# counterexample is worth finding. No deadline in CI: shared runners are slow and uneven.
settings.register_profile("ci", derandomize=True, database=None, deadline=None)
if os.environ.get("CI"):
    settings.load_profile("ci")

# Everything the code reads from the environment. GitHub Actions sets GITHUB_SHA and
# GITHUB_STEP_SUMMARY on every runner, so a test that inherited them would record the CI
# commit as the reference source and append its report to the real job summary. Each test
# starts without any of them and sets what it needs.
ENVIRONMENT = (
    "GITHUB_SHA",
    "GITHUB_STEP_SUMMARY",
    "SANITIZE_DENYLIST",
    "LEG_USER",
    "LEG_PASSWORD",
    "LEG_TRUST_PROXY",
    "LEG_OPENAI_BASE_URL",
    "OLLAMA_HOST",
    "PORT",
)


@pytest.fixture(autouse=True)
def hermetic_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def project() -> Project:
    return Project(ROOT)


@pytest.fixture(scope="session")
def workspace(project: Project) -> Workspace:
    return load_workspace(project)


def copy_project(target: Path) -> Project:
    for name in COPIED:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, target / name)
        else:
            shutil.copy2(source, target / name)
    return Project(target)


@pytest.fixture
def tmp_project(tmp_path: Path) -> Project:
    return copy_project(tmp_path / "repo")
