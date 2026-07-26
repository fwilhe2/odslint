# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`odslint` is a linter for LibreOffice Calc spreadsheets. It reads a spreadsheet, builds a document
model, runs rules over it, and reports diagnostics anchored to a sheet/cell — the spreadsheet
equivalent of `ruff` or `eslint`. It does **not** recalculate formulas; it reasons statically over
the stored document, and never writes to the file.

Both ODF packagings are first-class inputs and are detected by content, not extension:

- `.ods` — a ZIP package (`mimetype`, `content.xml`, `styles.xml`, `meta.xml`, `settings.xml`)
- `.fods` — a single flat XML file with the same `office:document` tree inlined

Everything after `loader.py` works on one unified model; no rule knows which packaging it came from,
and `tests/test_libreoffice_roundtrip.py` enforces that they produce identical diagnostics.

## Commands

Python 3.11+, `uv`, `lxml`. `uv sync` installs everything including dev tools.

```bash
uv run odslint path/to/book.ods            # lint
uv run odslint --format json path.ods      # machine-readable
uv run odslint --rule formula/magic-number path.ods   # one rule only
uv run odslint --list-rules

uv run odslint-clean tests/fixtures/*.fods # normalize flat XML in place
uv run odslint-clean --check path.fods     # exit 1 if it would change

uv run pytest                              # full suite (~3s)
uv run pytest tests/rules/test_magic_number.py::test_flags_a_hardcoded_rate
uv run pytest -k named_range

uv run ruff check . && uv run ruff format --check .
uv run mypy src                            # strict; keep it clean
```

Exit codes are a contract CI depends on: `0` clean, `1` findings at or above `fail-on`, `2` tool
error (unreadable file, bad config).

## Architecture

```
src/odslint/
  loader.py      ods (zip) + fods (flat) -> model.  All format quirks live here.
  model.py       Document / Sheet / Cell / NamedExpression / CellRange
  formula/       lexer.py (tokens + call contexts), reference.py, normalize.py (R1C1)
  rules/         base.py + one module per rule, self-registering via @register
  config.py      .odslintrc.toml discovery and validation
  suppress.py    cell-annotation directives
  engine.py      load -> rules -> suppression -> sorted diagnostics
  report.py      text and json
  cli.py
  cleanup.py     odslint-clean: the one thing here that writes to a file
  vendor/        third-party code, verbatim, under its own license
```

Rules are pure: they read the model and yield `Diagnostic`s, never mutate. Severity on a yielded
diagnostic is a placeholder — `engine.lint_document` overwrites it from config — so rules never read
configuration for anything but their own options. Suppression is applied centrally, so no rule ever
inspects annotations itself.

Rule ids are `category/kebab-name`. Categories: `formula`, `naming`, `data`, `structure`,
`portability`, `perf`, `meta`.

Adding a rule: subclass `Rule`, set `id` / `description` / `default_severity` / `default_options`,
decorate with `@register`, and import it in `rules/__init__.py`. Config validation rejects unknown
option names automatically, so `default_options` is the single source of truth for what is tunable.

## ODF specifics that will bite you

These are the non-obvious parts of the format. `tests/fixtures/repeats_and_merges.fods` encodes all
of them; loader changes should be checked against it first.

**Repeated cells and rows.** `table:number-columns-repeated` / `table:number-rows-repeated` compress
runs, and a single row element may legally claim `1048576` rows. Repeats are never materialized
blindly: empty runs only advance the logical index, and content-carrying runs are capped at
`loader.MAX_REPEAT` with a note in `Document.load_warnings`. Every naive ODS parser bug traces back
here. Note that the padding run LibreOffice writes at the end of every row and sheet is *empty*,
which is why the cap almost never triggers in practice.

**Merged cells.** The anchor carries `table:number-columns-spanned`/`-rows-spanned`; the shadowed
positions are `<table:covered-table-cell>` elements. They hold no content so the model drops them,
but they still consume column indices — skip them and every cell to the right shifts left.

**Rows can be nested** in `table:table-row-group` / `table:table-header-rows`, so the loader walks
with `.iter(TABLE_ROW)` rather than `iterchildren`. Document order is visual order.

**Formulas are OpenFormula, not Excel A1.** They live in `table:formula` as `of:=SUM([.A1:.A5])`.
References are bracketed and dot-qualified — `[.A1]`, `[Sheet2.A1]`, `[$'Sheet 2'.$A$1]`, ranges
`[.A1:.B2]`, whole columns `[.A:.A]`, external `['file:///…'#$Sheet.A1]`, dead ones `[#REF!]`. The
argument separator is `;`. **Never regex over formula text** — use `formula.lex()`, which is
quote-aware and never raises. `resolve()` returns `None` for external, invalid, and whole-column
references; treat that as "not analyzable", not as an error.

**Cell values are typed and cached.** `office:value-type` with the value in `office:value` /
`office:date-value` / `office:boolean-value`, plus display text in `<text:p>`. For a formula cell the
stored value is the *last calculated* result, possibly stale or an error
(`calcext:value-type="error"`). Annotation text is nested in `<office:annotation>` inside the cell,
so cell text is read from direct `text:p` children only — otherwise comments leak into values.

**Named expressions** appear at document scope (under `office:spreadsheet`) and sheet scope (under
`table:table`); sheet scope shadows document scope, which `Document.names_visible_from` implements.

**If you ever write `.ods` back out**, the ZIP must start with an uncompressed `STORED` `mimetype`
entry or LibreOffice rejects the file. `tests/helpers.py:fods_to_ods` is the worked example.

## Implemented rules

| Rule | Default | Notes |
| --- | --- | --- |
| `formula/prefer-named-range` | warning | Two narrow cases only: a reference whose target exactly matches an existing name, and an absolute/cross-sheet reference to a literal constant. Relative references to neighbours inside a fill block are deliberately **not** flagged — that would make the rule unusable. |
| `formula/inconsistent-in-range` | error | R1C1-normalizes each formula in a contiguous row/column run and flags the minority. Needs `min_run` cells and a `majority_ratio` agreement before it judges anything. |
| `formula/magic-number` | warning | Skips `STRUCTURAL_ARGS` — `ROUND(x;2)`, `VLOOKUP(…;3;0)` etc. are structure, not magic. |
| `data/number-stored-as-text` | error | Conservative: leading zeros, >15 digits, and internal spaces that are not thousands grouping all read as codes, not numbers. |

The false-positive budget is the thing to protect. A spreadsheet linter that cries wolf gets turned
off after one run, so prefer a narrow rule with a clear message over a broad one.

## Configuration and suppression

`.odslintrc.toml`, discovered upward from the linted file. One table per rule holding `severity`
(or `"off"`) plus that rule's own options. Unknown rule ids and unknown option names are hard
errors. See README for the format.

Spreadsheets have no comment syntax, so inline suppression uses **cell annotations** containing
`odslint-disable` (all rules on that cell) or `odslint-disable rule/id, other/id`.

## Flat-ODF cleanup (`odslint-clean`)

`cleanup.py` normalizes a `.fods` in place so it diffs like source: it drops unused styles, the
default number formats, `office:settings` / `office:scripts`, volatile `office:meta` children and
cached OLE bitmaps, renumbers the automatic table styles to a dense sequence, prunes the ~35
namespace declarations LibreOffice re-emits everywhere, and splits multi-attribute start tags onto
one line each.

This is **not** autofix — it is churn removal, it knows nothing about diagnostics, and the lint path
never calls into it. Its contract is that a cleaned file lints identically, which
`tests/test_cleanup.py` asserts over every fixture (model *and* diagnostics), and
`test_libreoffice_roundtrip.py` re-checks by having Calc reopen the cleaned files.

The engine is LibreOffice's `bin/flat-odf-cleanup.py`, vendored **byte-identical** at
`vendor/flat_odf_cleanup.py` so it can be resynced with a plain `curl`; it is MPL-2.0 and keeps its
own notice, while the rest of the project is MIT. Provenance, the resync command and the licensing
consequences are documented in the README's "Third-party code" section, with the license text in
`LICENSES/MPL-2.0.txt` — keep all three in step if you re-vendor. Consequences worth knowing before
touching it:

- Do not reformat, retype or tidy the vendored file. It is excluded from ruff and mypy in
  `pyproject.toml` for exactly that reason; `cleanup.py` is the typed boundary in front of it.
- It is a script, not a library: `collect_all_attribute` reads a module-global `root`, and logging
  reads a module-global `VERBOSE`. `clean_bytes` primes both before calling `remove_unused`. That is
  why cleaning is not reentrant.
- `clean_bytes` assembles the final document itself (`_assemble`) rather than using upstream's
  `tostring(root)`, which drops comments outside the root element — our fixtures open with one.

Dropping `office:settings` means `Document.settings` comes back empty on a cleaned file, which
matters for the `meta/iterative-calculation-enabled` idea in the backlog.

## Testing convention

**Fixtures are `.fods`, never checked-in binaries** — flat XML is text, so it diffs and reviews like
code. `tests/helpers.py` provides `fods_to_ods` (repackage as a real ZIP) and a compact builder
(`build`, `num`, `txt`, `formula`) for one-off cases in rule tests.

Each rule test module carries a positive case, a near-miss negative case, and a location assertion.
Repeat/merge/annotation edge cases belong in `test_loader.py`, not scattered across rule tests.

`test_libreoffice_roundtrip.py` converts every fixture with a real `soffice` and asserts the model
and the diagnostics are identical for both packagings. It is skipped when LibreOffice is absent. It
already earned its keep: the fixtures originally omitted `xmlns:of`, and LibreOffice "corrected"
that into `of:=of:=SUM(...)`. Hand-written fixtures drift from what Calc really writes — this is the
only thing that catches it.

**Gotcha when driving LibreOffice from here:** its Python script provider resolves `python3` from
`PATH`, and with the project venv first it fails to load any document containing an
`office:annotation`, reporting only `Error: source file could not be loaded`. Strip the venv from
`PATH` (see `_soffice_env`) or you will spend an hour blaming your fixtures.

## Rule backlog

Worth building next, roughly in value order. Several are cheap now that the loader and lexer exist.

- `formula/cached-error` — stored result is `#REF!`, `#DIV/0!`, `#N/A`, … (`Cell.error` is already
  populated, so this is a few lines)
- `formula/lookup-approximate-match` — `VLOOKUP`/`HLOOKUP` without the exact-match flag
- `formula/volatile-function` — `NOW`, `TODAY`, `RAND`; non-reproducible files
- `formula/indirect-offset` — defeats static analysis, including this linter's
- `formula/external-link` — `file://` / `http://` references to other workbooks
- `formula/nesting-depth`, `formula/length`, `formula/swallowed-error` (bare `IFERROR(x;"")`)
- `naming/default-sheet-name` (`Sheet1`, `Tabelle1`), `naming/unused-named-expression`,
  `naming/broken-named-expression`
- `data/inconsistent-column-type`, `data/untrimmed-string`, `data/merged-cell-in-data-range`
- `structure/hidden-sheet` (`Sheet.hidden` is already parsed), `structure/duplicate-header`,
  `structure/bloated-used-range`
- `portability/vendor-function` (`ORG.OPENOFFICE.*`, `COM.MICROSOFT.*`),
  `portability/embedded-macro` (`<script:module>`)
- `perf/whole-column-reference` (`Reference.is_whole_column` exists),
  `meta/iterative-calculation-enabled` (`Document.settings`)

Deferred by design: **autofix**. Rules stay pure and diagnostics stay the output; a fixer would sit
downstream and must preserve unknown ZIP parts byte-for-byte. `odslint-clean` is not a step towards
it — it rewrites a document without ever consulting a diagnostic.
