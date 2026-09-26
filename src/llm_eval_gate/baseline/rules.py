"""Signal rules: free-form postmortem prose -> canonical tokens.

Ported verbatim from postmortem-miner (MIT, same author), 31 bilingual regex rules, and
frozen here. Changing a rule changes the baseline, and the baseline is itself a gated
candidate: a pull request that edits a pattern and costs recall goes red like any other.

`RULES_SHA256` fingerprints the table so a fitted model can tell whether it was built on
the rules that are loaded right now.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from llm_eval_gate.jsonio import sha256_obj

# (token, pattern). One rule per row on purpose: adding a signal is a one-line change.
_RULES: tuple[tuple[str, str], ...] = (
    # --- resource -------------------------------------------------------------
    (
        "resource.cpu.saturated",
        r"cpu[^.\n]{0,30}?(?:8\d|9\d|100)\s?%|cpu\s+(?:alta|saturad\w+|pegged|maxed)",
    ),
    (
        "resource.memory.exhausted",
        r"\bOOM\b|OutOfMemory|heap\s+(?:estourou|cheio|exhaust\w+)|mem[oó]ria\s+esgotada",
    ),
    (
        "resource.gc.pressure",
        r"full\s+gc|gc\s+(?:pause|longo|thrash\w+)|old\s+gen\s+(?:cheia|full)",
    ),
    ("resource.disk.pressure", r"disco\s+(?:cheio|100)|disk\s+full|no\s+space\s+left"),
    # --- saturation -----------------------------------------------------------
    (
        "saturation.pool.exhausted",
        r"pool[^.\n]{0,25}?(\d{1,4})\s*/\s*\1\b|pool\s+(?:esgotad\w+|cheio|exhaust\w+)",
    ),
    ("saturation.pool.wait", r"waitcount|wait\s+count|fila\s+de\s+espera|blocking\s?time"),
    (
        "saturation.threads",
        r"threads?[^.\n]{0,20}?\d{3,}|thread\s+pool\s+(?:cheio|saturad\w+|exhaust\w+)",
    ),
    ("saturation.queue.backlog", r"backlog|fila\s+(?:acumul\w+|crescend\w+)|queue\s+depth"),
    # --- data store -----------------------------------------------------------
    (
        "store.lock.contention",
        r"\block(?:s|ing)?\b|deadlock|conten[çc][ãa]o|sess[õo]es?\s+em\s+lock",
    ),
    ("store.rollback.long", r"rollback|desfazend\w+\s+transa|undo\s+log"),
    ("store.query.slow", r"quer(?:y|ies)\s+lent\w+|slow\s+quer|p9[59][^.\n]{0,20}(?:s|ms)\b"),
    (
        "store.transaction.monolithic",
        r"transa[çc][ãa]o\s+(?:[úu]nica|gigante|monol[íi]tica)|single\s+(?:huge\s+)?transaction",
    ),
    # --- network --------------------------------------------------------------
    ("network.acl.block", r"security\s+group|\bacl\b|firewall|regra\s+de\s+entrada|ingress\s+rule"),
    ("network.lb.imbalance", r"stickiness|desbalance\w+|imbalanc\w+|hash\s+(?:ip|de\s+target)"),
    ("network.healthcheck.fail", r"health\s?check[^.\n]{0,20}(?:falh\w+|fail\w+|unhealthy)"),
    (
        "network.timeout.external",
        r"timeout[^.\n]{0,30}(?:externo|fora\s+da\s+vpn|from\s+outside)|connection\s+timed?\s?out",
    ),
    # --- application ----------------------------------------------------------
    (
        "app.npe",
        r"NoSuchElementException|NullPointerException|Optional\.get|unwrap\(\)\s+on\s+None",
    ),
    ("app.cast_error", r"ClassCastException|TypeError|cannot\s+cast"),
    ("app.batch_error", r"BatchUpdateException|batch\s+insert\s+(?:falh\w+|fail\w+)|-4329"),
    (
        "app.retry_storm",
        r"retry\s+(?:loop|storm|sem\s+backoff)|thundering\s+herd|reintent\w+\s+em\s+loop",
    ),
    (
        "app.error_swallowed",
        r"catch\s*\(\s*Exception[^)]*\)\s*\{\s*\}|except\s*:\s*pass|erro\s+engolid\w+",
    ),
    (
        "app.callback_missing",
        r"callback\s+(?:n[ãa]o\s+(?:enviad\w+|chegou)|missing)|retorno\s+n[ãa]o\s+enviad\w+",
    ),
    # --- lifecycle ------------------------------------------------------------
    (
        "lifecycle.deploy.recent",
        r"deploy\s+(?:recente|de\s+ontem|[àa]s\s+\d)|rollout|release\s+publicad\w+|\bdeployed\b|\bdeploy\b[^.\n]{0,25}(?:ontem|night|evening)",
    ),
    (
        "lifecycle.cert.expired",
        r"certifica[dt]\w*[^.\n]{0,60}expir\w+|\b(?:ssl|tls)\b[^.\n]{0,30}expir\w+|keystore\s+expir",
    ),
    (
        "lifecycle.schedule.window",
        r"schedule|janela\s+de\s+(?:manuten|hor[áa]rio)|\bcron\b|start\s?/\s?stop",
    ),
    (
        "lifecycle.restart.reactive",
        r"restart\s+(?:reativo|manual|sequencial)|reiniciad\w+\s+(?:manualmente|pelo\s+time)",
    ),
    # --- workload -------------------------------------------------------------
    (
        "workload.traffic.spike",
        r"pico\s+de\s+(?:tr[áa]fego|requisi|volume)|spike|surto\s+de\s+carga",
    ),
    (
        "workload.payload.large",
        r"\d{2,}\s?MB\b|payload\s+grande|arquivo\s+grande|remessa\s+(?:grande|de\s+\d{4,})",
    ),
    ("workload.batch.window", r"\bbatch\b|processamento\s+noturno|job\s+agendad\w+"),
    # --- topology -------------------------------------------------------------
    (
        "topology.single_node",
        r"(?:apenas|somente|s[óo])\s+(?:1|um)\s+n[óo]|isolad\w+\s+em\s+(?:um|1)\s+n[óo]|single\s+node|only\s+(?:1|one)\s+node",
    ),
    ("topology.all_nodes", r"todos\s+os\s+n[óo]s|toda\s+a\s+frota|all\s+nodes|fleet[- ]wide"),
)

_COMPILED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (token, re.compile(pattern, re.IGNORECASE)) for token, pattern in _RULES
)

RULES_SHA256 = sha256_obj([[token, pattern] for token, pattern in _RULES])
_MAX_EVIDENCE = 160


@dataclass(frozen=True, slots=True)
class Signal:
    token: str
    evidence: str


def known_tokens() -> tuple[str, ...]:
    return tuple(token for token, _ in _RULES)


def _evidence(text: str, match: re.Match[str]) -> str:
    start = max(match.start() - 40, 0)
    end = min(match.end() + 40, len(text))
    if start > 0:
        space = text.find(" ", start, match.start())
        start = space + 1 if space != -1 else start
    if end < len(text):
        space = text.rfind(" ", match.end(), end)
        end = space if space != -1 else end
    return " ".join(text[start:end].split())[:_MAX_EVIDENCE].strip()


def extract(text: str) -> tuple[Signal, ...]:
    """First match per token wins; output follows the rule-table order."""
    found: list[Signal] = []
    for token, pattern in _COMPILED:
        match = pattern.search(text)
        if match is not None:
            found.append(Signal(token=token, evidence=_evidence(text, match)))
    return tuple(found)


def tokens(text: str) -> frozenset[str]:
    return frozenset(signal.token for signal in extract(text))
