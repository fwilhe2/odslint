"""Command line entry point.

Exit codes are part of the contract CI depends on:
``0`` clean, ``1`` findings at or above ``fail-on``, ``2`` tool error.
``--diff`` follows ``odslint-clean --check`` instead: ``1`` means "there is
something to apply".
"""

from __future__ import annotations

import argparse
import difflib
import sys
from collections.abc import Sequence
from pathlib import Path

from odslint import __version__, report
from odslint.config import Config, ConfigError
from odslint.diagnostics import Diagnostic
from odslint.engine import lint_file, select_rules, should_fail
from odslint.fixer import FixError, fix_file, plan_fixes, preview
from odslint.loader import LoadError
from odslint.package import PackageError
from odslint.rules import REGISTRY, all_rules
from odslint.rules.base import Rule

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

#: A fix can free up a cell another fix wanted, so ``--fix`` keeps going until
#: nothing more applies. Bounded so a rule whose fix re-triggers itself cannot
#: spin forever.
MAX_FIX_PASSES = 5


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
    parser.add_argument(
        "--fix",
        action="store_true",
        help="apply safe fixes and report what is left",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="show what --fix would change, without writing",
    )
    parser.add_argument(
        "--unsafe-fixes",
        action="store_true",
        help="also apply fixes that can change a stored value or a formula's meaning",
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

    if args.fix and args.diff:
        parser.error("--fix and --diff are mutually exclusive")

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
    if args.diff:
        return _run_diff(args, config, rules)

    diagnostics: list[Diagnostic] = []
    fixed = 0
    failed = False

    for path in args.paths:
        try:
            found = lint_file(path, config, rules)
            if args.fix:
                found, applied = _apply_fixes(path, config, rules, found, args.unsafe_fixes)
                fixed += applied
            diagnostics.extend(found)
        except (LoadError, FixError, PackageError) as exc:
            print(f"odslint: {exc}", file=sys.stderr)
            failed = True

    color = not args.no_color and sys.stdout.isatty()
    print(report.render(diagnostics, args.format, color=color))
    if fixed and args.format == "text":
        # json has to stay a bare array, so the count only goes to the human format.
        print(f"Fixed {fixed} problem{'s' if fixed != 1 else ''}.")

    if failed:
        return EXIT_ERROR
    return EXIT_FINDINGS if should_fail(diagnostics, config) else EXIT_OK


def _apply_fixes(
    path: Path,
    config: Config,
    rules: Sequence[Rule],
    diagnostics: list[Diagnostic],
    unsafe: bool,
) -> tuple[list[Diagnostic], int]:
    """Fix ``path`` in place, returning what is still wrong and how much was fixed.

    Every pass re-lints from the file that was just written, which is what proves
    the edits produced a document that still loads. If anything goes wrong the
    original bytes go back — a linter that leaves a spreadsheet unopenable is
    worse than one that changes nothing.
    """
    original = path.read_bytes()
    remaining = diagnostics
    fixed = 0
    try:
        for _ in range(MAX_FIX_PASSES):
            plan = plan_fixes(remaining, unsafe=unsafe)
            if plan.is_empty:
                break
            fix_file(path, plan.edits)
            fixed += len(plan.fixed)
            remaining = lint_file(path, config, rules)
    except (FixError, PackageError, LoadError):
        path.write_bytes(original)
        raise
    return remaining, fixed


def _run_diff(args: argparse.Namespace, config: Config, rules: Sequence[Rule]) -> int:
    """Print what ``--fix`` would do. Writes nothing; exits 1 if there is anything."""
    chunks: list[str] = []
    failed = False

    for path in args.paths:
        try:
            found = lint_file(path, config, rules)
            plan = plan_fixes(found, unsafe=args.unsafe_fixes)
            if plan.is_empty:
                continue
            before, after, label = preview(path, plan.edits)
            chunks.extend(
                difflib.unified_diff(
                    before.decode("utf-8", "replace").splitlines(keepends=True),
                    after.decode("utf-8", "replace").splitlines(keepends=True),
                    fromfile=label,
                    tofile=label,
                )
            )
        except (LoadError, FixError, PackageError) as exc:
            print(f"odslint: {exc}", file=sys.stderr)
            failed = True

    if chunks:
        sys.stdout.write("".join(chunks))
        if not chunks[-1].endswith("\n"):
            sys.stdout.write("\n")

    if failed:
        return EXIT_ERROR
    return EXIT_FINDINGS if chunks else EXIT_OK


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
