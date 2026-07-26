"""Command line entry point.

Exit codes are part of the contract CI depends on:
``0`` clean, ``1`` findings at or above ``fail-on``, ``2`` tool error.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from odslint import __version__, report
from odslint.config import Config, ConfigError
from odslint.diagnostics import Diagnostic
from odslint.engine import lint_file, select_rules, should_fail
from odslint.loader import LoadError
from odslint.rules import REGISTRY, all_rules

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="odslint",
        description="Lint LibreOffice Calc spreadsheets (.ods and flat .fods).",
    )
    parser.add_argument("paths", nargs="*", type=Path, help="spreadsheets to lint")
    parser.add_argument("--format", choices=report.FORMATS, default="text", help="output format")
    parser.add_argument(
        "--rule",
        action="append",
        metavar="ID",
        help="run only this rule (repeatable); overrides config enablement",
    )
    parser.add_argument(
        "--fail-on",
        choices=["error", "warning", "info", "never"],
        help="lowest severity that makes the run fail",
    )
    parser.add_argument("--config", type=Path, help="path to a config file")
    parser.add_argument(
        "--no-config", action="store_true", help="ignore any discovered config file"
    )
    parser.add_argument("--list-rules", action="store_true", help="list rules and exit")
    parser.add_argument("--no-color", action="store_true", help="disable colored output")
    parser.add_argument("--version", action="version", version=f"odslint {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_rules:
        _print_rules()
        return EXIT_OK

    if not args.paths:
        parser.error("no spreadsheets given")

    try:
        config = _resolve_config(args)
    except ConfigError as exc:
        print(f"odslint: {exc}", file=sys.stderr)
        return EXIT_ERROR

    unknown = [r for r in (args.rule or []) if r not in REGISTRY]
    if unknown:
        print(f"odslint: unknown rule {unknown[0]!r}", file=sys.stderr)
        return EXIT_ERROR

    rules = select_rules(config, args.rule)
    diagnostics: list[Diagnostic] = []
    failed = False

    for path in args.paths:
        try:
            diagnostics.extend(lint_file(path, config, rules))
        except LoadError as exc:
            print(f"odslint: {exc}", file=sys.stderr)
            failed = True

    color = not args.no_color and sys.stdout.isatty()
    print(report.render(diagnostics, args.format, color=color))

    if failed:
        return EXIT_ERROR
    return EXIT_FINDINGS if should_fail(diagnostics, config) else EXIT_OK


def _resolve_config(args: argparse.Namespace) -> Config:
    if args.no_config:
        config = Config()
    elif args.config is not None:
        config = Config.load(args.config)
    else:
        config = Config.discover(args.paths[0].resolve())

    if args.fail_on is not None:
        from odslint.diagnostics import Severity

        config.fail_on = None if args.fail_on == "never" else Severity(args.fail_on)
    return config


def _print_rules() -> None:
    width = max(len(cls.id) for cls in all_rules())
    for cls in all_rules():
        print(f"{cls.id:<{width}}  {cls.default_severity.value:<7}  {cls.description}")


if __name__ == "__main__":
    raise SystemExit(main())
