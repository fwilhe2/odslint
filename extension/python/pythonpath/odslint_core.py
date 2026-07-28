"""Everything the extension does that does not need UNO.

Kept separate from ``odslint_ext`` so it can be tested with plain pytest — the
UNO half needs a running LibreOffice, this half does not, and almost all of the
logic that can actually be wrong lives here.

Deliberately dependency-free and conservative about syntax: this module is
imported by LibreOffice's own Python, whose version is whatever the platform
build shipped, not the one the project develops against.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

#: Exit codes from the odslint CLI.
EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

#: Cell background colours for the optional highlight, as 0xRRGGBB.
HIGHLIGHT_COLORS = {
    "error": 0xFFD4D4,
    "warning": 0xFFF0C0,
    "info": 0xD6ECFF,
}


class OdslintNotFound(Exception):
    """No usable odslint executable could be located."""


class OdslintFailed(Exception):
    """odslint ran but reported a tool error."""


class Finding:
    """One diagnostic, as the JSON report describes it."""

    __slots__ = (
        "rule",
        "severity",
        "sheet",
        "cell",
        "row",
        "column",
        "message",
        "hint",
        "fix",
    )

    def __init__(self, payload):
        self.rule = payload.get("rule") or ""
        self.severity = payload.get("severity") or "warning"
        self.sheet = payload.get("sheet") or ""
        self.cell = payload.get("cell")
        self.row = payload.get("row")
        self.column = payload.get("column")
        self.message = payload.get("message") or ""
        self.hint = payload.get("hint")
        self.fix = payload.get("fix")

    @property
    def is_cell_anchored(self):
        """Sheet-level findings have no row/column and cannot be navigated to."""
        return self.row is not None and self.column is not None

    @property
    def location(self):
        return f"{self.sheet}!{self.cell}" if self.cell else self.sheet

    @property
    def is_fixable(self):
        return bool(self.fix and self.fix.get("edits"))

    @property
    def is_safe_fix(self):
        return bool(self.fix) and self.fix.get("applicability") == "safe"

    def label(self):
        """One line for the findings list."""
        mark = " [*]" if self.is_fixable else ""
        return f"{self.location.ljust(14)}  {self.severity[:4]}{mark}  {self.message}"

    def sort_key(self):
        return (
            SEVERITY_ORDER.get(self.severity, 9),
            self.sheet,
            self.row if self.row is not None else -1,
            self.column if self.column is not None else -1,
            self.rule,
        )


def parse_report(text):
    """Turn the ``--format json`` array into :class:`Finding` objects."""
    if not text.strip():
        return []
    payload = json.loads(text)
    return [Finding(item) for item in payload]


def find_interpreter(configured=None, env=None):
    """The command prefix that runs odslint, as a list.

    LibreOffice's Python is the platform interpreter, not the project's
    virtualenv, so the extension never imports odslint — it runs whatever
    ``odslint`` the user has installed. A configured path wins; otherwise we look
    where a ``pip install --user`` or ``uv tool install`` would have put it.
    """
    env = os.environ if env is None else env

    if configured:
        expanded = os.path.expanduser(configured)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return [expanded]
        raise OdslintNotFound(f"configured odslint path is not executable: {expanded}")

    found = shutil.which("odslint", path=env.get("PATH"))
    if found:
        return [found]

    for candidate in (
        os.path.expanduser("~/.local/bin/odslint"),
        "/usr/local/bin/odslint",
        "/usr/bin/odslint",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return [candidate]

    raise OdslintNotFound(
        "odslint is not installed, or not on PATH.\n\n"
        "Install it with:    pip install --user odslint\n"
        "or:                 uv tool install odslint\n\n"
        "If it lives somewhere else, set the path in Tools > odslint > Settings."
    )


def build_command(interpreter, path, unsafe=False):
    command = list(interpreter) + ["--format", "json", "--fail-on", "never"]
    if unsafe:
        # Only affects which fixes are reported; the extension never asks the
        # CLI to write, it applies edits itself through UNO.
        command.append("--unsafe-fixes")
    command.append(path)
    return command


def run_odslint(interpreter, path, unsafe=False, timeout=120):
    """Lint ``path`` and return its findings.

    Raises :class:`OdslintFailed` on a tool error so the caller can show the
    reason rather than an empty list, which would read as "your file is clean".
    """
    command = build_command(interpreter, path, unsafe=unsafe)
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout)
    except OSError as exc:
        raise OdslintFailed(f"could not run {command[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise OdslintFailed(f"odslint did not finish within {timeout} seconds") from exc

    stdout = result.stdout.decode("utf-8", "replace")
    stderr = result.stderr.decode("utf-8", "replace").strip()

    if result.returncode == EXIT_ERROR:
        raise OdslintFailed(stderr or "odslint reported an error")
    try:
        findings = parse_report(stdout)
    except ValueError as exc:
        raise OdslintFailed(f"could not read odslint's output: {exc}\n\n{stderr}") from exc

    findings.sort(key=lambda f: f.sort_key())
    return findings


def collect_edits(findings, unsafe=False):
    """The edits to apply, one cell at a time, first claim winning.

    Mirrors ``odslint.fixer.plan_fixes`` — two rules wanting the same cell would
    otherwise have the second silently overwrite the first.
    """
    edits = []
    claimed = set()
    for finding in findings:
        if not finding.is_fixable:
            continue
        if not finding.is_safe_fix and not unsafe:
            continue
        candidates = finding.fix["edits"]
        targets = set((e["sheet"], e["row"], e["column"]) for e in candidates)
        if targets & claimed:
            continue
        claimed |= targets
        edits.extend(candidates)
    return edits


# -- settings ---------------------------------------------------------------

DEFAULT_SETTINGS = {
    "interpreter": "",
    "lint_on_save": False,
    "highlight": True,
    "unsafe_fixes": False,
}


def load_settings(path):
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(path, "rb") as handle:
            stored = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError):
        return settings
    if isinstance(stored, dict):
        for key in DEFAULT_SETTINGS:
            if key in stored:
                settings[key] = stored[key]
    return settings


def save_settings(path, settings):
    directory = os.path.dirname(path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    payload = dict(DEFAULT_SETTINGS)
    payload.update(settings)
    with open(path, "wb") as handle:
        handle.write(json.dumps(payload, indent=2).encode("utf-8"))
