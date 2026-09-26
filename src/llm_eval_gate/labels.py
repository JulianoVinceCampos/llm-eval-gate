"""The label space: single source of truth for the classifier, the prompt and the UI.

The eight recurring failure modes come from the postmortem-miner corpus. `none` is a real
answer, not a fallback: a classifier that forces every incident into a pattern is broken,
and the dataset carries one-off incidents on purpose to catch that.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Label(StrEnum):
    POOL_LOCK = "pool-lock"
    HEAP_OOM = "heap-oom"
    RETRY_STORM = "retry-storm"
    ROLLBACK = "rollback"
    CERT = "cert"
    ACL = "acl"
    LB_APP = "lb-app"
    SLOW_QUERY = "slow-query"
    NONE = "none"


# Prediction value for an output that could not be parsed into a Label. Kept out of the
# enum on purpose: it is an outcome of the model, never a valid answer of the dataset.
INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class LabelInfo:
    label: Label
    title: str
    mechanism: str


CATALOG: tuple[LabelInfo, ...] = (
    LabelInfo(
        Label.POOL_LOCK,
        "Pool de conexões esgotado com lock no banco",
        "The database connection pool is exhausted on every application node at once, "
        "sessions hold locks on a hot table and database CPU stays high. Usually one code "
        "path multiplies the number of statements.",
    ),
    LabelInfo(
        Label.HEAP_OOM,
        "Estouro de heap com payload grande",
        "One application node runs out of heap memory while processing a single very large "
        "request or file, garbage collection thrashes, and the rest of the fleet is healthy.",
    ),
    LabelInfo(
        Label.RETRY_STORM,
        "Tempestade de retry de job agendado",
        "Scheduled or background jobs fail and retry immediately without backoff, flooding "
        "the database while the machine running the jobs is mostly idle.",
    ),
    LabelInfo(
        Label.ROLLBACK,
        "Rollback longo de transação monolítica",
        "The database is busy undoing one huge long-running transaction; restarting does not "
        "help because recovery resumes the undo work.",
    ),
    LabelInfo(
        Label.CERT,
        "Certificado TLS expirado na borda",
        "An expired TLS certificate on a public listener: every target fails its health "
        "probe at the same minute, external clients fail, internal traffic is fine, and "
        "nothing was deployed recently.",
    ),
    LabelInfo(
        Label.ACL,
        "Regra de rede bloqueando acesso externo",
        "Network access rules block traffic from outside or from the load balancer, while "
        "the application itself reports no error.",
    ),
    LabelInfo(
        Label.LB_APP,
        "Desbalanceamento com bug de aplicação",
        "Load concentrates on one node after session affinity is recalculated, combined with "
        "an application bug in a lookup that returns empty and breaks a partner callback.",
    ),
    LabelInfo(
        Label.SLOW_QUERY,
        "Query lenta após manutenção",
        "Queries become slow right after database maintenance or a statistics refresh "
        "because a stale execution plan survived; database CPU is high without connection "
        "pool saturation.",
    ),
    LabelInfo(
        Label.NONE,
        "Incidente pontual, sem padrão",
        "A one-off incident that does not match any of the recurring failure modes above.",
    ),
)

_BY_VALUE: dict[str, Label] = {label.value: label for label in Label}


def all_labels() -> tuple[Label, ...]:
    """Labels in declaration order. The order is the tie-breaker everywhere."""
    return tuple(Label)


def family_labels() -> tuple[Label, ...]:
    """The eight recurring patterns, without `none`."""
    return tuple(label for label in Label if label is not Label.NONE)


def prediction_values() -> tuple[str, ...]:
    """Every value an observation can carry: the labels plus `invalid`, in stable order."""
    return (*(label.value for label in Label), INVALID)


def parse_label(value: str) -> Label | None:
    """Strict parse. Case and surrounding space are forgiven; synonyms are not.

    Underscore is accepted for hyphen (`pool_lock`) because it is a tokenizer artefact, not
    a different answer. Anything else is not a label, and inventing a mapping for it would
    inflate the model score with the parser's opinion.
    """
    normalized = value.strip().lower().replace("_", "-")
    return _BY_VALUE.get(normalized)


def info(label: Label) -> LabelInfo:
    return next(item for item in CATALOG if item.label is label)
