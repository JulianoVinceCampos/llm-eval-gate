"""Deterministic corpus generator.

Three splits, three jobs:

- `train`: templated vocabulary, used only to fit the rule baseline. Never evaluated.
- `in-dist`: same templated vocabulary, fresh seed. The comfortable case.
- `hard`: the paraphrase lexicon, opaque titles, omissions, noise, negated distractors,
  code-switching and typos. The case the rules were not written for.

Two leaks are closed by construction. The case id is an opaque hash, and the text never
carries an `id:` line with the family name in it (the postmortem-miner template did,
which is harmless for a regex and fatal for a language model reading the document).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from llm_eval_gate.datasets.families import FAMILIES, ONE_OFFS, Family, OneOff, family_for
from llm_eval_gate.datasets.lexicon import (
    DISTRACTORS,
    NOISE,
    ONE_OFFS_HARD,
    OPAQUE_TITLES,
    SECTION_HEADERS,
    Phrasings,
    hard_family_for,
)
from llm_eval_gate.domain import SPLIT_HARD, SPLIT_IN, SPLIT_TRAIN, Case
from llm_eval_gate.jsonio import sha256_text
from llm_eval_gate.labels import Label, family_labels
from llm_eval_gate.rng import Rng

GENERATOR_VERSION = 1


@dataclass(frozen=True, slots=True)
class SplitSpec:
    name: str
    per_family: int
    none_count: int
    seed: int
    hard: bool


DEFAULT_SPECS: tuple[SplitSpec, ...] = (
    SplitSpec(SPLIT_TRAIN, per_family=12, none_count=24, seed=101, hard=False),
    SplitSpec(SPLIT_IN, per_family=20, none_count=40, seed=202, hard=False),
    SplitSpec(SPLIT_HARD, per_family=30, none_count=60, seed=303, hard=True),
)

# Fictitious, domain-neutral service names. Nothing here names a real system.
SERVICES = ("svc-orders", "svc-catalog", "svc-checkout", "svc-notify", "svc-search")
SEVERITIES = ("P1", "P1", "P2")
_PT_RATIO = 0.5
# Templated members of a family drop at most one signal, like postmortem-miner does.
_DROP_CHANCE = 0.5
_MIN_SIGNALS_TO_DROP = 3
# Hard split knobs. Written down so the dataset card and the code cannot disagree.
_HARD_EN, _HARD_PT = 0.35, 0.70  # cumulative thresholds; the rest is code-switched
_HARD_TRIGGER = 0.7
_HARD_MITIGATION = 0.8
_HARD_NOISE = 0.6
_HARD_DISTRACTOR = 0.45
_HARD_FREE_FORM = 0.5
_HARD_TYPO = 0.3
# Share of hard sentences that keep the templated wording (see lexicon.py).
_HARD_TEMPLATE_KEEP = 0.35

TEMPLATE = """---
title: {title}
date: {date}
severity: {severity}
service: {service}
tags: [postmortem, synthetic]
---

# {title}

## Impact

{impact}

## Observed signals

{signals}

## Trigger

{trigger}

## Mitigation

{mitigation}

## Root cause

{root_cause}
"""

_WORD = re.compile(r"[A-Za-z]{7,}")


def _values(rng: Rng) -> dict[str, object]:
    month = 1 + rng.below(9)
    day = 1 + rng.below(27)
    return {
        "cpu": rng.choice((84, 88, 92, 96, 98)),
        "mins": rng.choice((35, 45, 60, 90)),
        "pool": rng.choice((40, 60, 80)),
        "node": f"node-{1 + rng.below(5)}",
        "hour": 9 + rng.below(11),
        "mb": rng.choice((18, 24, 31, 44)),
        "count": rng.choice((1200, 8400, 20100, 26800)),
        "threads": rng.choice((1800, 2600, 3100)),
        "days": rng.choice((1, 2, 3)),
        "ms": rng.choice((40, 60, 90)),
        "ms2": rng.choice((900, 1400, 2600)),
        "ip": "203.0.113.",
        "secs": rng.choice((3, 5, 30)),
        "hours": rng.choice((2, 3, 4)),
        "service": rng.choice(SERVICES),
        "severity": rng.choice(SEVERITIES),
        "date": f"2026-{month:02d}-{day:02d}",
    }


def _case_id(spec: SplitSpec, index: int) -> str:
    return "c" + sha256_text(f"{GENERATOR_VERSION}|{spec.name}|{spec.seed}|{index}")[:12]


# --- templated ------------------------------------------------------------------------


def _render_family_templated(family: Family, rng: Rng) -> tuple[str, str]:
    values = _values(rng)
    portuguese = rng.random() < _PT_RATIO
    chosen = list(family.signals_pt if portuguese else family.signals_en)
    if len(chosen) > _MIN_SIGNALS_TO_DROP and rng.random() < _DROP_CHANCE:
        chosen.pop(rng.below(len(chosen)))
    signals = "\n".join(f"- {line.format(**values)}" for line in chosen)
    if family.root_cause_open:
        root_cause = (
            "Causa raiz estrutural nao tratada - a recorrencia deve continuar."
            if portuguese
            else "Root cause not addressed structurally - recurrence is expected."
        )
    else:
        root_cause = "Causa raiz tratada." if portuguese else "Root cause addressed."
    text = TEMPLATE.format(
        title=family.title_pt if portuguese else family.title_en,
        date=values["date"],
        severity=values["severity"],
        service=values["service"],
        impact=(
            "Operacoes de clientes ficaram degradadas durante a janela do incidente."
            if portuguese
            else "Customer operations were degraded for the duration of the incident."
        ),
        signals=signals,
        trigger=family.trigger_pt if portuguese else family.trigger_en,
        mitigation=family.mitigation_pt if portuguese else family.mitigation_en,
        root_cause=root_cause,
    )
    return text, "pt" if portuguese else "en"


def _render_one_off_templated(one_off: OneOff, rng: Rng) -> tuple[str, str]:
    values = _values(rng)
    portuguese = rng.random() < _PT_RATIO
    prose = (one_off.text_pt if portuguese else one_off.text_en).format(**values)
    text = TEMPLATE.format(
        title=one_off.title_pt if portuguese else one_off.title_en,
        date=values["date"],
        severity="P2",
        service=values["service"],
        impact=(
            "Impacto limitado, ocorrencia unica."
            if portuguese
            else "Limited impact, single occurrence."
        ),
        signals=f"- {prose}",
        trigger=(
            "Ocorrencia unica, sem gatilho recorrente identificado."
            if portuguese
            else "Single occurrence, no recurring trigger identified."
        ),
        mitigation=(
            "Tratado na hora pelo plantao."
            if portuguese
            else "Handled inline by the on-call engineer."
        ),
        root_cause="Causa raiz tratada." if portuguese else "Root cause addressed.",
    )
    return text, "pt" if portuguese else "en"


# --- hard -----------------------------------------------------------------------------


def _lang_mode(rng: Rng) -> str:
    roll = rng.random()
    if roll < _HARD_EN:
        return "en"
    if roll < _HARD_PT:
        return "pt"
    return "mixed"


def _sentence_lang(mode: str, rng: Rng) -> str:
    if mode == "mixed":
        return "pt" if rng.chance(0.5) else "en"
    return mode


def _pick(phrasings: Phrasings, lang: str, rng: Rng, values: dict[str, object]) -> str:
    return rng.choice(phrasings.pick_pool(lang)).format(**values)


def _extras(
    own: Label | None, mode: str, rng: Rng, values: dict[str, object], operators: list[str]
) -> list[str]:
    extras: list[str] = []
    if rng.chance(_HARD_NOISE):
        lang = _sentence_lang(mode, rng)
        pool = NOISE.pick_pool(lang)
        count = 1 + rng.below(2)
        extras.extend(line.format(**values) for line in rng.sample(pool, count))
        operators.append("noise")
    if rng.chance(_HARD_DISTRACTOR):
        other = rng.choice([label for label in family_labels() if label is not own])
        extras.append(_pick(DISTRACTORS[other], _sentence_lang(mode, rng), rng, values))
        operators.append(f"distractor:{other.value}")
    return extras


def _assemble(
    mode: str,
    values: dict[str, object],
    body: list[str],
    trigger: str | None,
    mitigation: str | None,
    extras: list[str],
    rng: Rng,
    operators: list[str],
) -> str:
    header_lang = _sentence_lang(mode, rng)
    title = _pick(OPAQUE_TITLES, header_lang, rng, values)
    if header_lang == "pt":
        meta = f"Severidade: {values['severity']} | Servico: {values['service']}"
    else:
        meta = f"Severity: {values['severity']} | Service: {values['service']}"
    sentences = list(body)
    for extra in extras:
        sentences.insert(rng.below(len(sentences) + 1), extra)
    if rng.chance(_HARD_FREE_FORM):
        operators.append("free-form")
        paragraphs = [" ".join(sentences)]
        tail = [part for part in (trigger, mitigation) if part]
        if tail:
            paragraphs.append(" ".join(tail))
        return f"# {title}\n\n{meta}\n\n" + "\n\n".join(paragraphs) + "\n"
    headers = rng.choice(SECTION_HEADERS.pick_pool(header_lang)).split("|")
    lines = [f"# {title}", "", meta, "", f"## {headers[0]}", ""]
    lines.extend(f"- {sentence}" for sentence in sentences)
    if trigger:
        lines += ["", f"## {headers[1]}", "", trigger]
    if mitigation:
        lines += ["", f"## {headers[2]}", "", mitigation]
    return "\n".join(lines) + "\n"


def _maybe_typo(text: str, rng: Rng, operators: list[str]) -> str:
    """Swap two inner letters of one long word, outside the title line."""
    if not rng.chance(_HARD_TYPO):
        return text
    head, _, body = text.partition("\n")
    words = list(_WORD.finditer(body))
    if not words:
        return text
    match = rng.choice(words)
    word = match.group(0)
    i = 1 + rng.below(len(word) - 3)
    swapped = word[:i] + word[i + 1] + word[i] + word[i + 2 :]
    if swapped == word:
        return text
    operators.append("typo")
    return head + "\n" + body[: match.start()] + swapped + body[match.end() :]


def _either(
    template_en: str,
    template_pt: str,
    paraphrase: Phrasings,
    lang: str,
    rng: Rng,
    values: dict[str, object],
) -> tuple[str, bool]:
    """A templated or a paraphrased sentence. Returns (sentence, used_template)."""
    if rng.chance(_HARD_TEMPLATE_KEEP):
        return (template_pt if lang == "pt" else template_en).format(**values), True
    return _pick(paraphrase, lang, rng, values), False


def _render_family_hard(label: Label, rng: Rng) -> tuple[str, str, tuple[str, ...]]:
    family = hard_family_for(label)
    template = family_for(label)
    values = _values(rng)
    mode = _lang_mode(rng)
    operators = ["opaque-title"]
    if mode == "mixed":
        operators.append("code-switch")
    slots = len(family.observations)
    keep = max(2, slots - (1 + rng.below(2)))
    if keep < slots:
        operators.append("omission")
    body: list[str] = []
    templated = 0
    for index in rng.sample(list(range(slots)), keep):
        sentence, used = _either(
            template.signals_en[index],
            template.signals_pt[index],
            family.observations[index],
            _sentence_lang(mode, rng),
            rng,
            values,
        )
        body.append(sentence)
        templated += used
    trigger = mitigation = None
    if rng.chance(_HARD_TRIGGER):
        trigger, used = _either(
            template.trigger_en,
            template.trigger_pt,
            family.trigger,
            _sentence_lang(mode, rng),
            rng,
            values,
        )
        templated += used
    if rng.chance(_HARD_MITIGATION):
        mitigation, used = _either(
            template.mitigation_en,
            template.mitigation_pt,
            family.mitigation,
            _sentence_lang(mode, rng),
            rng,
            values,
        )
        templated += used
    operators.append("vocab-mix" if templated else "vocab-shift")
    extras = _extras(label, mode, rng, values, operators)
    text = _assemble(mode, values, body, trigger, mitigation, extras, rng, operators)
    text = _maybe_typo(text, rng, operators)
    return text, mode, tuple(operators)


def _render_one_off_hard(one_off: OneOff, rng: Rng) -> tuple[str, str, tuple[str, ...]]:
    values = _values(rng)
    mode = _lang_mode(rng)
    operators = ["opaque-title"]
    if mode == "mixed":
        operators.append("code-switch")
    sentence, used = _either(
        one_off.text_en,
        one_off.text_pt,
        ONE_OFFS_HARD[one_off.key],
        _sentence_lang(mode, rng),
        rng,
        values,
    )
    operators.append("vocab-mix" if used else "vocab-shift")
    body = [sentence]
    extras = _extras(None, mode, rng, values, operators)
    text = _assemble(mode, values, body, None, None, extras, rng, operators)
    text = _maybe_typo(text, rng, operators)
    return text, mode, tuple(operators)


# --- public API -----------------------------------------------------------------------


def generate_split(spec: SplitSpec) -> list[Case]:
    rng = Rng("dataset", GENERATOR_VERSION, spec.name, spec.seed)
    cases: list[Case] = []
    index = 0
    for family in FAMILIES:
        for _ in range(spec.per_family):
            if spec.hard:
                text, lang, operators = _render_family_hard(family.label, rng)
            else:
                text, lang = _render_family_templated(family, rng)
                operators = ()
            cases.append(
                Case(_case_id(spec, index), spec.name, family.label, lang, text, operators)
            )
            index += 1
    for k in range(spec.none_count):
        one_off = ONE_OFFS[k % len(ONE_OFFS)]
        if spec.hard:
            text, lang, operators = _render_one_off_hard(one_off, rng)
        else:
            text, lang = _render_one_off_templated(one_off, rng)
            operators = ()
        cases.append(Case(_case_id(spec, index), spec.name, Label.NONE, lang, text, operators))
        index += 1
    return sorted(cases, key=lambda case: case.id)


def generate_all(specs: tuple[SplitSpec, ...] = DEFAULT_SPECS) -> dict[str, list[Case]]:
    return {spec.name: generate_split(spec) for spec in specs}
