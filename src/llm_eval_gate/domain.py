"""Value objects shared by every layer. No IO, no policy, just the shape of the data."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from llm_eval_gate.jsonio import SchemaError, sha256_obj
from llm_eval_gate.labels import INVALID, Label

SPLIT_TRAIN = "train"
SPLIT_IN = "in-dist"
SPLIT_HARD = "hard"
EVAL_SPLITS: tuple[str, ...] = (SPLIT_IN, SPLIT_HARD)
ALL_SPLITS: tuple[str, ...] = (SPLIT_TRAIN, SPLIT_IN, SPLIT_HARD)

STAGE_PRODUCTION = "production"
STAGE_EXPERIMENTAL = "experimental"
STAGES: tuple[str, ...] = (STAGE_PRODUCTION, STAGE_EXPERIMENTAL)

RUN_SCHEMA = 1


@dataclass(frozen=True, slots=True)
class Case:
    """One incident to classify. The label comes from the generator, never from a model."""

    id: str
    split: str
    label: Label
    lang: str
    text: str
    operators: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "split": self.split,
            "label": self.label.value,
            "lang": self.lang,
            "text": self.text,
            "operators": list(self.operators),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Case:
        return cls(
            id=str(data["id"]),
            split=str(data["split"]),
            label=Label(str(data["label"])),
            lang=str(data["lang"]),
            text=str(data["text"]),
            operators=tuple(str(op) for op in data.get("operators", ())),
        )


@dataclass(frozen=True, slots=True)
class Prediction:
    """A parsed answer. `label=None` means the output was not a valid label."""

    label: Label | None
    evidence: str = ""

    @property
    def value(self) -> str:
        return self.label.value if self.label is not None else INVALID

    @property
    def format_ok(self) -> bool:
        return self.label is not None


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a classifier returns for one call."""

    prediction: Prediction
    latency_ms: float
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(frozen=True, slots=True)
class Observation:
    """One classification of one case in one repeat, as it enters the run record."""

    case_id: str
    repeat: int
    predicted: str
    latency_ms: float
    tokens_in: int
    tokens_out: int
    cost_usd: float

    @property
    def format_ok(self) -> bool:
        return self.predicted != INVALID

    def to_dict(self) -> dict[str, Any]:
        # Rounded on the way out: float noise in the last digit would otherwise make two
        # identical runs hash differently on two machines.
        return {
            "case_id": self.case_id,
            "repeat": self.repeat,
            "predicted": self.predicted,
            "latency_ms": round(self.latency_ms, 3),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": round(self.cost_usd, 9),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Observation:
        return cls(
            case_id=str(data["case_id"]),
            repeat=int(data["repeat"]),
            predicted=str(data["predicted"]),
            latency_ms=float(data["latency_ms"]),
            tokens_in=int(data["tokens_in"]),
            tokens_out=int(data["tokens_out"]),
            cost_usd=float(data["cost_usd"]),
        )


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Everything a candidate produced over the evaluation cases."""

    candidate: str
    stage: str
    classifier: Mapping[str, Any]
    dataset_sha256: str
    repeats: int
    observations: tuple[Observation, ...]
    schema: int = RUN_SCHEMA
    _by_case: dict[str, tuple[Observation, ...]] = field(
        default_factory=dict, compare=False, repr=False
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "candidate": self.candidate,
            "stage": self.stage,
            "classifier": dict(self.classifier),
            "dataset_sha256": self.dataset_sha256,
            "repeats": self.repeats,
            "observations": [obs.to_dict() for obs in self.observations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunRecord:
        schema = int(data.get("schema", 0))
        if schema != RUN_SCHEMA:
            raise SchemaError(f"unsupported run schema {schema}, expected {RUN_SCHEMA}")
        return cls(
            candidate=str(data["candidate"]),
            stage=str(data["stage"]),
            classifier=dict(data["classifier"]),
            dataset_sha256=str(data["dataset_sha256"]),
            repeats=int(data["repeats"]),
            observations=tuple(Observation.from_dict(obs) for obs in data["observations"]),
            schema=schema,
        )

    @property
    def fingerprint(self) -> str:
        return sha256_obj(self.to_dict())

    def by_case(self) -> dict[str, tuple[Observation, ...]]:
        if not self._by_case:
            grouped: dict[str, list[Observation]] = {}
            for obs in self.observations:
                grouped.setdefault(obs.case_id, []).append(obs)
            for case_id, items in grouped.items():
                self._by_case[case_id] = tuple(sorted(items, key=lambda o: o.repeat))
        return self._by_case

    @property
    def case_ids(self) -> frozenset[str]:
        return frozenset(self.by_case())
