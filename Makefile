.DEFAULT_GOAL := help
PY ?= python3

.PHONY: help install check lint fmt test cov gate calibrate readme serve docker clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install the package with dev extras and the pre-commit hooks
	$(PY) -m pip install -e ".[dev]"
	pre-commit install --install-hooks

check: ## Everything the pull request pipeline runs, in the same order
	$(PY) tools/tasks.py check

lint: ## Sanitize, typography, ruff and mypy
	$(PY) tools/tasks.py lint

fmt: ## Apply formatting and safe fixes
	$(PY) -m ruff check --fix src tests tools
	$(PY) -m ruff format src tests tools

test: ## Test suite
	$(PY) -m pytest

cov: ## Tests with coverage and the ratchet
	$(PY) tools/tasks.py test

gate: ## Artifact checks and the gate itself, as CI runs them
	$(PY) tools/tasks.py gate

calibrate: ## Re-run the calibration of the gate (about a minute)
	$(PY) -m llm_eval_gate.cli calibrate

readme: ## Regenerate the results block of the README
	$(PY) -m llm_eval_gate.cli readme

serve: ## Dashboard on http://127.0.0.1:8000
	$(PY) -m llm_eval_gate.cli serve

docker: ## Build and run the image the deploy uses
	docker compose up --build

clean: ## Remove build and test artefacts
	rm -rf out dist build .pytest_cache .ruff_cache .mypy_cache .hypothesis htmlcov coverage.xml coverage.json .coverage evals/runs
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
