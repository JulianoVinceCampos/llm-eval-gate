"""Command line. Thin on purpose: parsing and printing here, decisions in pipeline.py.

Exit codes: 0 pass, 1 the gate or a determinism check failed, 2 usage or configuration
error. CI tells "the model regressed" apart from "the config is broken" by the code alone.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from llm_eval_gate import __version__
from llm_eval_gate.baseline.signature import LeakageError
from llm_eval_gate.calibration import CalibrationSpec
from llm_eval_gate.config import ConfigError
from llm_eval_gate.domain import EVAL_SPLITS
from llm_eval_gate.factory import PendingEvidenceError, ProviderMode
from llm_eval_gate.jsonio import SchemaError, write_json, write_text
from llm_eval_gate.labels import CATALOG
from llm_eval_gate.pipeline import (
    Project,
    accept_reference,
    build_datasets,
    calibrate,
    candidate,
    compare_results,
    evaluate_candidate,
    fit_baseline,
    load_workspace,
    render_readme,
    run_ci,
    run_scenarios,
    save_run,
)
from llm_eval_gate.prompt import PromptError
from llm_eval_gate.providers.base import ProviderError
from llm_eval_gate.report import OUTCOME_PT, candidate_markdown, ci_markdown, comparison_markdown
from llm_eval_gate.runner import ProgressPrinter
from llm_eval_gate.spec import SpecError

Handler = Callable[[Project, argparse.Namespace], int]


def _utf8_streams() -> None:
    # Redirected output on Windows defaults to the ANSI code page, which cannot encode half
    # of what a report prints. Reconfigure once, at the edge.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _problems(problems: Sequence[str], ok: str) -> int:
    if problems:
        for problem in problems:
            print(f"fora de sincronia: {problem}", file=sys.stderr)
        return 1
    print(ok)
    return 0


def _append_summary(path: str | None, markdown: str) -> None:
    target = path or os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with Path(target).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown + "\n")


def cmd_datasets(project: Project, args: argparse.Namespace) -> int:
    if args.check:
        return _problems(
            build_datasets(project, check=True), "datasets: em sincronia com o gerador"
        )
    build_datasets(project, check=False)
    print("datasets: gerados em datasets/")
    return 0


def cmd_fit(project: Project, args: argparse.Namespace) -> int:
    if args.check:
        return _problems(fit_baseline(project, check=True), "baseline: artefato em sincronia")
    fit_baseline(project, check=False)
    print(f"baseline: {project.model_path.relative_to(project.root).as_posix()} atualizado")
    return 0


def cmd_run(project: Project, args: argparse.Namespace) -> int:
    ws = load_workspace(project)
    config = candidate(project, args.name)
    result = evaluate_candidate(ws, config, ProviderMode(args.mode), listener=ProgressPrinter())
    if result.run is None:
        print(f"{config.name}: {result.message}", file=sys.stderr)
        return 1 if result.blocking else 0
    path = Path(args.out) if args.out else save_run(project, result.run)
    if args.out:
        write_json(path, result.run.to_dict())
    print(candidate_markdown(result))
    print(f"run salvo em {path}")
    return 0


def cmd_gate(project: Project, args: argparse.Namespace) -> int:
    ws = load_workspace(project)
    config = candidate(project, args.name)
    result = evaluate_candidate(ws, config, ProviderMode(args.mode))
    markdown = candidate_markdown(result)
    print(markdown)
    if args.json:
        write_json(Path(args.json), result.summary_dict())
    _append_summary(args.summary, markdown)
    return 1 if result.blocking else 0


def cmd_ci(project: Project, args: argparse.Namespace) -> int:
    outcome = run_ci(project, ProviderMode(args.mode))
    markdown = ci_markdown(outcome)
    print(markdown)
    if args.json:
        write_json(Path(args.json), outcome.to_dict())
    if args.markdown:
        write_text(Path(args.markdown), markdown)
    _append_summary(args.summary, markdown)
    return outcome.exit_code


def cmd_accept(project: Project, args: argparse.Namespace) -> int:
    path = accept_reference(
        project,
        args.name,
        mode=ProviderMode(args.mode),
        run_path=Path(args.run) if args.run else None,
        note=args.note,
    )
    print(f"referência aceita: {path.relative_to(project.root).as_posix()}")
    print("revise o diff e commite: aceitar referência é uma decisão, não um efeito colateral")
    return 0


def cmd_compare(project: Project, args: argparse.Namespace) -> int:
    ws = load_workspace(project)
    a = evaluate_candidate(ws, candidate(project, args.a), ProviderMode(args.mode))
    b = evaluate_candidate(ws, candidate(project, args.b), ProviderMode(args.mode))
    splits = [args.split] if args.split else list(EVAL_SPLITS)
    printed = False
    for split in splits:
        comparison = compare_results(ws, a, b, split)
        if comparison is not None:
            print(comparison_markdown(comparison))
            printed = True
    if not printed:
        print("sem casos pareados entre os dois candidatos", file=sys.stderr)
        return 2
    return 0


def cmd_calibrate(project: Project, args: argparse.Namespace) -> int:
    spec = CalibrationSpec(trials=args.trials) if args.trials else None

    def progress(done: int, total: int) -> None:
        print(f"calibração: célula {done}/{total}", file=sys.stderr)

    result, problems = calibrate(project, check=args.check, spec=spec, progress=progress)
    if args.check:
        return _problems(problems, "calibração: resultado igual ao commitado")
    print(f"calibração gravada: {len(result.cells)} células")
    for method in result.spec.methods:
        print(
            f"  {method}: aprovação indevida na margem até {result.worst_false_pass(method):.1%}, "
            f"alarme falso até {result.worst_false_alarm(method):.1%}"
        )
    return 0


def cmd_scenarios(project: Project, args: argparse.Namespace) -> int:
    outcomes = run_scenarios(load_workspace(project))
    failed = 0
    for outcome in outcomes:
        mark = "ok " if outcome.as_expected else "ERR"
        expected = OUTCOME_PT[outcome.scenario.expected.value]
        print(
            f"[{mark}] {outcome.scenario.name}: esperado {expected}, "
            f"obtido {OUTCOME_PT[outcome.verdict.value]}"
        )
        failed += not outcome.as_expected
    return 1 if failed else 0


def cmd_readme(project: Project, args: argparse.Namespace) -> int:
    if args.check:
        return _problems(render_readme(project, check=True), "README: bloco de resultados em dia")
    problems = render_readme(project, check=False)
    return _problems(problems, "README: bloco de resultados regenerado")


def cmd_labels(project: Project, args: argparse.Namespace) -> int:
    for item in CATALOG:
        print(f"{item.label.value:12s} {item.title}")
    return 0


def cmd_serve(project: Project, args: argparse.Namespace) -> int:
    from llm_eval_gate.webapp import serve  # noqa: PLC0415 - server code only when serving

    port = args.port if args.port is not None else int(os.environ.get("PORT", "8000"))
    serve(project, host=args.host, port=port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-eval-gate",
        description="Gate de CI que reprova o PR quando a qualidade da saída do LLM regride.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--root", default=".", help="raiz do projeto (default: diretório atual)")
    sub = parser.add_subparsers(dest="command", required=True)
    modes = [mode.value for mode in ProviderMode]

    def add(name: str, handler: Handler, help_text: str) -> argparse.ArgumentParser:
        command = sub.add_parser(name, help=help_text, description=help_text)
        command.set_defaults(handler=handler)
        return command

    add("datasets", cmd_datasets, "gera ou verifica os splits").add_argument(
        "--check", action="store_true"
    )
    add("fit", cmd_fit, "ajusta ou verifica o baseline de regras").add_argument(
        "--check", action="store_true"
    )

    run = add("run", cmd_run, "executa um candidato e salva o run")
    run.add_argument("name")
    run.add_argument("--mode", choices=modes, default=ProviderMode.REPLAY.value)
    run.add_argument("--out")

    gate = add("gate", cmd_gate, "avalia um candidato contra referência e spec")
    gate.add_argument("name")
    gate.add_argument("--mode", choices=modes, default=ProviderMode.REPLAY.value)
    gate.add_argument("--json")
    gate.add_argument("--summary")

    ci = add("ci", cmd_ci, "tudo o que o pipeline de PR faz, na ordem")
    ci.add_argument("--mode", choices=modes, default=ProviderMode.REPLAY.value)
    ci.add_argument("--json")
    ci.add_argument("--markdown")
    ci.add_argument("--summary")

    accept = add("accept", cmd_accept, "aceita uma nova referência (decisão explícita)")
    accept.add_argument("name")
    accept.add_argument("--mode", choices=modes, default=ProviderMode.REPLAY.value)
    accept.add_argument("--run", help="aceitar um run já gravado em vez de executar")
    accept.add_argument("--note", default="")

    compare = add("compare", cmd_compare, "compara dois candidatos caso a caso")
    compare.add_argument("a")
    compare.add_argument("b")
    compare.add_argument("--split", choices=list(EVAL_SPLITS))
    compare.add_argument("--mode", choices=modes, default=ProviderMode.REPLAY.value)

    calibrate_cmd = add("calibrate", cmd_calibrate, "mede erro e poder do próprio gate")
    calibrate_cmd.add_argument("--check", action="store_true")
    calibrate_cmd.add_argument("--trials", type=int)

    add("scenarios", cmd_scenarios, "roda os cenários de validação do gate")
    add("readme", cmd_readme, "regenera ou verifica o bloco de resultados do README").add_argument(
        "--check", action="store_true"
    )
    add("labels", cmd_labels, "lista os rótulos")

    serve = add("serve", cmd_serve, "sobe o dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    project = Project(Path(args.root).resolve())
    if not project.root.is_dir():
        print(f"erro: raiz do projeto não encontrada: {project.root}", file=sys.stderr)
        return 2
    handler: Handler = args.handler
    try:
        return handler(project, args)
    except PendingEvidenceError as error:
        print(f"pendente: {error}", file=sys.stderr)
        return 2
    except (ConfigError, SpecError, PromptError, LeakageError, ProviderError, SchemaError) as error:
        print(f"erro: {error}", file=sys.stderr)
        return 2
    except FileNotFoundError as error:
        print(f"erro: arquivo não encontrado: {error.filename}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as error:
        print(f"erro: JSON inválido: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
