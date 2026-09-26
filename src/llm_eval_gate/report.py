"""Markdown renderings: pull-request summary, per-candidate report and the README block.

Everything is derived from result objects, never typed by hand, and the output is
deterministic, so the README block can be regenerated in CI and diffed against what is
committed. The number in the README is the number the pipeline computed.
"""

from __future__ import annotations

from collections.abc import Sequence

from llm_eval_gate.calibration import CalibrationResult
from llm_eval_gate.domain import EVAL_SPLITS
from llm_eval_gate.results import CandidateResult, CIOutcome, Comparison
from llm_eval_gate.scenarios import ScenarioOutcome

OUTCOME_PT = {
    "pass": "PASSA",
    "fail": "REPROVA",
    "inconclusive": "INCONCLUSIVO",
    "pending": "PENDENTE",
    "error": "ERRO",
    "evaluated": "AVALIADO",
}
STATUS_PT = {"met": "atende", "not_met": "não atende", "no_evidence": "sem evidência"}
METHOD_PT = {
    "tango": "Score de Tango",
    "agresti-min": "Wald+2",
    "bootstrap": "Bootstrap percentil",
}


def pct(value: float | None, digits: int = 1) -> str:
    return "n/d" if value is None else f"{value * 100:.{digits}f}%"


def pp(value: float) -> str:
    return f"{value * 100:+.1f} p.p."


def interval(lo: float, hi: float) -> str:
    return f"[{lo * 100:.1f}, {hi * 100:.1f}]"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _row(*cells: object) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _rule(columns: int) -> str:
    return "|" + "---|" * columns


def _outcome(result: CandidateResult) -> str:
    return OUTCOME_PT.get(result.outcome, result.outcome)


def _yes(flag: bool) -> str:
    return "sim" if flag else "não"


def method_name(method: str) -> str:
    return METHOD_PT.get(method, method)


def candidate_markdown(result: CandidateResult) -> str:
    config = result.config
    lines = [
        f"### {config.name} ({config.stage}): {_outcome(result)}",
        "",
        _cell(config.description) if config.description else "",
    ]
    if result.report is None:
        lines += ["", f"_{result.message}_" if result.message else "_sem execução_", ""]
        return "\n".join(lines) + "\n"
    headers = (
        "Split",
        "Casos",
        "Acurácia (IC95)",
        "Macro-F1",
        "Formato válido",
        "Instabilidade",
        "p95",
        "Custo/chamada",
    )
    lines += ["", _row(*headers), _rule(len(headers))]
    for split in EVAL_SPLITS:
        m = result.metrics.get(split)
        if m is None:
            continue
        lines.append(
            _row(
                split,
                m.n_cases,
                f"{pct(m.accuracy)} {interval(m.accuracy_lo, m.accuracy_hi)}",
                f"{m.macro_f1:.3f}",
                pct(m.format_valid_rate),
                pct(m.instability_rate),
                f"{m.latency_p95_ms:.1f} ms",
                f"${m.cost_per_call_usd:.6f}",
            )
        )
    lines += ["", _row("Verificação", "Obrigatória", "Resultado", "Detalhe"), _rule(4)]
    for check in result.report.checks:
        lines.append(
            _row(
                _cell(check.title),
                _yes(check.enforced),
                OUTCOME_PT[check.verdict.value],
                _cell(check.summary),
            )
        )
    failing = [row for row in result.trace if row.failing_cases]
    if failing:
        lines += ["", "Casos que puxam requisitos para baixo:", ""]
        for row in failing:
            lines.append(f"- `{row.requirement.id}`: {', '.join(row.failing_cases)}")
    return "\n".join(lines) + "\n"


def ci_markdown(outcome: CIOutcome) -> str:
    verdict = "REPROVA" if outcome.exit_code else "PASSA"
    lines = [f"## llm-eval-gate: {verdict}", ""]
    if outcome.problems:
        lines += ["Artefatos fora de sincronia com o gerador:", ""]
        lines += [f"- {problem}" for problem in outcome.problems]
        return "\n".join(lines) + "\n"
    lines += [
        _row("Candidato", "Estágio", "Resultado", "Bloqueia", "in-dist", "hard"),
        _rule(6),
    ]
    for result in outcome.results:
        accuracies = [
            pct(result.metrics[split].accuracy) if split in result.metrics else "n/d"
            for split in EVAL_SPLITS
        ]
        row = (result.name, result.config.stage, _outcome(result), _yes(result.blocking))
        lines.append(_row(*row, *accuracies))
    lines.append("")
    for result in outcome.results:
        lines.append(candidate_markdown(result))
    return "\n".join(lines) + "\n"


def comparison_markdown(comparison: Comparison) -> str:
    mcnemar = comparison.mcnemar
    lines = [
        f"#### {comparison.b} contra {comparison.a} [{comparison.split}]",
        "",
        f"- casos pareados: {comparison.n_cases}",
        f"- acurácia: {pct(comparison.a_accuracy)} contra {pct(comparison.b_accuracy)}, "
        f"diferença {pp(comparison.delta)} (IC95 {interval(comparison.lo, comparison.hi)})",
        f"- McNemar exato: {mcnemar.better} casos que só {comparison.b} acerta, "
        f"{mcnemar.worse} que só {comparison.a} acerta, p = {mcnemar.p_two_sided:.2g}",
    ]
    if comparison.operators:
        lines += ["", _row("Operador", "Casos", comparison.a, comparison.b), _rule(4)]
        for op, n, a_acc, b_acc in comparison.operators:
            lines.append(_row(op, n, pct(a_acc), pct(b_acc)))
    return "\n".join(lines) + "\n"


def calibration_markdown(result: CalibrationResult) -> str:
    spec = result.spec
    methods = spec.methods
    lines = [
        f"Margem {spec.margin * 100:.1f} p.p., alfa unilateral {spec.alpha:.2f}, "
        f"{spec.trials} simulações por célula, acurácia de referência "
        f"{pct(spec.reference_accuracy, 0)}, correlação {spec.correlation:.2f}. Os métodos "
        f"decidem sobre os mesmos pares simulados; o bootstrap usa {spec.resamples} "
        "reamostragens.",
        "",
        f"Como ler: na linha de efeito {pp(-spec.margin)}, igual à margem, `passa` é aprovação "
        f"indevida e deveria ficar perto de {pct(spec.alpha, 0)}. Com efeito zero, `reprova` é "
        "alarme falso, e tudo o que não é `passa` bloqueia o PR quando "
        '`on_inconclusive = "fail"`.',
        "",
    ]
    lines += [
        f"- {method_name(m)}: aprovação indevida na margem de até "
        f"{pct(result.worst_false_pass(m), 1)}, alarme falso de até "
        f"{pct(result.worst_false_alarm(m), 1)}."
        for m in methods
    ]
    headers = ["Casos", "Efeito real"]
    headers += [f"{method_name(m)} (passa / inconcl. / reprova)" for m in methods]
    lines += ["", _row(*headers), _rule(len(headers))]
    for n in spec.sizes:
        for effect in spec.effects:
            cells: list[str] = []
            for m in methods:
                cell = result.cell(m, n, effect)
                cells.append(
                    "n/d"
                    if cell is None
                    else f"{pct(cell.pass_rate, 0)} / {pct(cell.inconclusive_rate, 0)} "
                    f"/ {pct(cell.fail_rate, 0)}"
                )
            lines.append(_row(n, pp(effect), *cells))
    return "\n".join(lines) + "\n"


def scenarios_markdown(outcomes: Sequence[ScenarioOutcome]) -> str:
    lines = [_row("Cenário", "Esperado", "Obtido"), _rule(3)]
    for outcome in outcomes:
        lines.append(
            _row(
                _cell(outcome.scenario.title),
                OUTCOME_PT[outcome.scenario.expected.value],
                OUTCOME_PT[outcome.verdict.value],
            )
        )
    return "\n".join(lines) + "\n"


def readme_block(
    evidence: Sequence[CandidateResult],
    comparisons: Sequence[Comparison],
    calibration: CalibrationResult | None,
    scenarios: Sequence[ScenarioOutcome],
) -> str:
    """The results section of the README, regenerated and diffed by CI."""
    headers = (
        "Candidato",
        "Estágio",
        "in-dist (IC95)",
        "hard (IC95)",
        "Formato válido",
        "Instabilidade",
        "p95",
    )
    lines = [
        "Gerado por `llm-eval-gate readme` a partir das referências aceitas em "
        "`evals/reference/`. O CI regenera este bloco e reprova se ele divergir do que está "
        "commitado.",
        "",
        _row(*headers),
        _rule(len(headers)),
    ]
    for result in evidence:
        cells = []
        for split in EVAL_SPLITS:
            m = result.metrics.get(split)
            cells.append(
                f"{pct(m.accuracy)} {interval(m.accuracy_lo, m.accuracy_hi)}" if m else "n/d"
            )
        hard = result.metrics.get("hard") or result.metrics.get("in-dist")
        tail = ["n/d", "n/d", "n/d"]
        if hard:
            tail = [
                pct(hard.format_valid_rate),
                pct(hard.instability_rate),
                f"{hard.latency_p95_ms:.1f} ms",
            ]
        lines.append(_row(f"`{result.name}`", result.config.stage, *cells, *tail))
    if comparisons:
        lines += ["", "Comparação pareada (mesmos casos, caso a caso):", ""]
        for comparison in comparisons:
            lines.append(comparison_markdown(comparison))
    if scenarios:
        lines += ["", "Cenários de validação do gate (modelo sintético, resultado conhecido):", ""]
        lines.append(scenarios_markdown(scenarios))
    if calibration is not None:
        lines += ["", "Calibração do gate (erro e poder medidos, não presumidos):", ""]
        lines.append(calibration_markdown(calibration))
    return "\n".join(lines).rstrip() + "\n"
