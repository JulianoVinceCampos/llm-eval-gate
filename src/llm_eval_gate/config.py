"""Candidate configuration: one TOML file per model/prompt/params combination under test.

Strict on purpose. An unknown key is an error, not a warning, because a typo in
`temprature` that silently falls back to a default is a gate measuring something other
than what the file says.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_eval_gate.classifiers import SyntheticProfile
from llm_eval_gate.domain import ALL_SPLITS, STAGES
from llm_eval_gate.jsonio import read_text

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]{0,62}$")
KINDS = ("rules", "llm", "synthetic")


class ConfigError(ValueError):
    """The candidate file is malformed."""


@dataclass(frozen=True, slots=True)
class Prices:
    """Shadow price per million tokens. Local models cost zero in cash, not in compute."""

    input_per_mtok_usd: float = 0.0
    output_per_mtok_usd: float = 0.0

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in * self.input_per_mtok_usd + tokens_out * self.output_per_mtok_usd) / 1e6


@dataclass(frozen=True, slots=True)
class SampleSpec:
    fraction: float = 1.0
    seed: int = 0


@dataclass(frozen=True, slots=True)
class Budget:
    max_calls: int | None = None
    max_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LLMSettings:
    provider: str
    model: str
    prompt: str
    cassette: str
    temperature: float = 0.0
    seed: int = 0
    max_tokens: int = 96
    timeout_s: float = 120.0
    base_url: str | None = None
    api_key_env: str | None = None


@dataclass(frozen=True, slots=True)
class CandidateConfig:
    name: str
    stage: str
    description: str
    kind: str
    repeats: int
    sample: SampleSpec
    prices: Prices
    budget: Budget
    model_path: str | None = None
    llm: LLMSettings | None = None
    synthetic: SyntheticProfile | None = None
    source: str = ""


def _reject_unknown(table: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown keys {unknown}")


def _table(data: Mapping[str, Any], key: str, where: str, *, required: bool) -> dict[str, Any]:
    value = data.get(key)
    if value is None:
        if required:
            raise ConfigError(f"{where}: missing [{key}] table")
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: [{key}] must be a table")
    return value


def _str(table: Mapping[str, Any], key: str, where: str, default: str | None = None) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: `{key}` must be a non-empty string")
    return value


def _num(
    table: Mapping[str, Any], key: str, where: str, default: float, lo: float, hi: float
) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{where}: `{key}` must be a number")
    if not lo <= float(value) <= hi:
        raise ConfigError(f"{where}: `{key}` must be within [{lo}, {hi}], got {value}")
    return float(value)


def _int(table: Mapping[str, Any], key: str, where: str, default: int, lo: int, hi: int) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: `{key}` must be an integer")
    if not lo <= value <= hi:
        raise ConfigError(f"{where}: `{key}` must be within [{lo}, {hi}], got {value}")
    return value


def _optional_int(table: Mapping[str, Any], key: str, where: str) -> int | None:
    if key not in table:
        return None
    return _int(table, key, where, 0, 1, 10**12)


def _relative_path(value: str, where: str) -> str:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ConfigError(f"{where}: paths are relative to the project root, got {value!r}")
    return path.as_posix()


def parse_candidate(data: Mapping[str, Any], source: str = "<memory>") -> CandidateConfig:
    _reject_unknown(data, {"candidate", "classifier", "sample", "prices", "budget"}, source)
    head = _table(data, "candidate", source, required=True)
    _reject_unknown(head, {"name", "stage", "description"}, f"{source} [candidate]")
    name = _str(head, "name", source)
    if not NAME_RE.match(name):
        raise ConfigError(f"{source}: name {name!r} must match {NAME_RE.pattern}")
    stage = _str(head, "stage", source)
    if stage not in STAGES:
        raise ConfigError(f"{source}: stage must be one of {list(STAGES)}")
    description = str(head.get("description", ""))

    body = _table(data, "classifier", source, required=True)
    where = f"{source} [classifier]"
    kind = _str(body, "kind", where)
    if kind not in KINDS:
        raise ConfigError(f"{where}: kind must be one of {list(KINDS)}")

    sample_t = _table(data, "sample", source, required=False)
    _reject_unknown(sample_t, {"fraction", "seed"}, f"{source} [sample]")
    sample = SampleSpec(
        fraction=_num(sample_t, "fraction", f"{source} [sample]", 1.0, 0.01, 1.0),
        seed=_int(sample_t, "seed", f"{source} [sample]", 0, 0, 2**31),
    )
    prices_t = _table(data, "prices", source, required=False)
    _reject_unknown(prices_t, {"input_per_mtok_usd", "output_per_mtok_usd"}, f"{source} [prices]")
    prices = Prices(
        input_per_mtok_usd=_num(
            prices_t, "input_per_mtok_usd", f"{source} [prices]", 0.0, 0.0, 1e4
        ),
        output_per_mtok_usd=_num(
            prices_t, "output_per_mtok_usd", f"{source} [prices]", 0.0, 0.0, 1e4
        ),
    )
    budget_t = _table(data, "budget", source, required=False)
    _reject_unknown(budget_t, {"max_calls", "max_tokens"}, f"{source} [budget]")
    budget = Budget(
        max_calls=_optional_int(budget_t, "max_calls", f"{source} [budget]"),
        max_tokens=_optional_int(budget_t, "max_tokens", f"{source} [budget]"),
    )

    def build(
        repeats: int,
        *,
        model_path: str | None = None,
        llm: LLMSettings | None = None,
        synthetic: SyntheticProfile | None = None,
    ) -> CandidateConfig:
        return CandidateConfig(
            name=name,
            stage=stage,
            description=description,
            kind=kind,
            repeats=repeats,
            sample=sample,
            prices=prices,
            budget=budget,
            model_path=model_path,
            llm=llm,
            synthetic=synthetic,
            source=source,
        )

    if kind == "rules":
        _reject_unknown(body, {"kind", "model"}, where)
        return build(1, model_path=_relative_path(_str(body, "model", where), where))
    if kind == "llm":
        allowed = {
            "kind",
            "provider",
            "model",
            "prompt",
            "cassette",
            "temperature",
            "seed",
            "max_tokens",
            "timeout_s",
            "base_url",
            "api_key_env",
            "repeats",
        }
        _reject_unknown(body, allowed, where)
        llm = LLMSettings(
            provider=_str(body, "provider", where),
            model=_str(body, "model", where),
            prompt=_relative_path(_str(body, "prompt", where), where),
            cassette=_relative_path(_str(body, "cassette", where), where),
            temperature=_num(body, "temperature", where, 0.0, 0.0, 2.0),
            seed=_int(body, "seed", where, 0, 0, 2**31),
            max_tokens=_int(body, "max_tokens", where, 96, 8, 4096),
            timeout_s=_num(body, "timeout_s", where, 120.0, 1.0, 900.0),
            base_url=str(body["base_url"]) if "base_url" in body else None,
            api_key_env=str(body["api_key_env"]) if "api_key_env" in body else None,
        )
        return build(_int(body, "repeats", where, 1, 1, 20), llm=llm)
    allowed = {
        "kind",
        "accuracy",
        "instability",
        "invalid_rate",
        "correlation",
        "latency_ms",
        "seed",
        "repeats",
    }
    _reject_unknown(body, allowed, where)
    accuracy_t = _table(body, "accuracy", where, required=True)
    accuracy: dict[str, float] = {}
    for split in accuracy_t:
        if split not in ALL_SPLITS:
            raise ConfigError(f"{where}: accuracy split {split!r} is not one of {list(ALL_SPLITS)}")
        accuracy[split] = _num(accuracy_t, split, where, 0.0, 0.0, 1.0)
    profile = SyntheticProfile(
        accuracy=accuracy,
        instability=_num(body, "instability", where, 0.0, 0.0, 1.0),
        invalid_rate=_num(body, "invalid_rate", where, 0.0, 0.0, 1.0),
        correlation=_num(body, "correlation", where, 1.0, 0.0, 1.0),
        latency_ms=_num(body, "latency_ms", where, 400.0, 0.0, 600_000.0),
        seed=_int(body, "seed", where, 0, 0, 2**31),
    )
    return build(_int(body, "repeats", where, 1, 1, 20), synthetic=profile)


def load_candidate(path: Path) -> CandidateConfig:
    try:
        data = tomllib.loads(read_text(path))
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{path.name}: invalid TOML: {error}") from None
    config = parse_candidate(data, source=path.name)
    if path.stem != config.name:
        raise ConfigError(f"{path.name}: file name must match candidate name {config.name!r}")
    return config


def load_candidates(directory: Path) -> list[CandidateConfig]:
    return [load_candidate(path) for path in sorted(directory.glob("*.toml"))]
