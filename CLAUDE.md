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

uv run odslint --fix path.ods              # apply safe fixes
uv run odslint --fix --unsafe-fixes p.ods  # ...and the rest
uv run odslint --diff path.ods             # preview, write nothing

uv run odslint-clean tests/fixtures/*.fods # normalize flat XML in place
uv run odslint-clean --check path.fods     # exit 1 if it would change

python tools/build_oxt.py                  # -> dist/odslint-VERSION.oxt
unopkg add --force dist/odslint-*.oxt

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
                 Also owns iter_row_runs / iter_cell_runs, the repeat-aware walk
                 the fixer reuses — there must be exactly one of those.
  model.py       Document / Sheet / Cell / NamedExpression / CellRange
  diagnostics.py Severity, Diagnostic, and the Fix / Edit / Applicability trio
  formula/       lexer.py (tokens + call contexts), reference.py, normalize.py (R1C1),
                 edit.py (splice, translate, to_a1)
  rules/         base.py + one module per rule, self-registering via @register
  config.py      .odslintrc.toml discovery and validation
  suppress.py    cell-annotation directives
  engine.py      load -> rules -> suppression -> sorted diagnostics
  fixer.py       Edits -> XML.  Locates cells, splits repeats, writes.
  package.py     .ods ZIP rewriting that preserves every part it did not touch
  report.py      text and json
  cli.py
  cleanup.py     odslint-clean: churn removal, unrelated to diagnostics
  vendor/        forked-from-elsewhere code, under its own license

extension/       the LibreOffice Calc add-on, built into an .oxt
  python/odslint_ext.py             UNO glue: dispatch, sidebar panel, highlight
  python/pythonpath/odslint_core.py pure logic, unit-testable without UNO
  config/*.xcu                      menu, protocol handler, sidebar deck
tools/build_oxt.py
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

The engine at `vendor/flat_odf_cleanup.py` is a **fork of LibreOffice's `bin/flat-odf-cleanup.py`,
and this repo is its canonical home** — it is not resynced from anywhere, so change it here. It is
MPL-2.0 and keeps LibreOffice's own notice, while the rest of the project is MIT. On top of
upstream's passes it is quiet by default (`log()` gated on `VERBOSE`), cleans files in place, splits
start tags to one attribute per line, prunes namespaces without stripping the `xmlns:of` that
`table:formula` needs textually, and additionally drops unused number-format styles, volatile
`office:meta` children, cached OLE bitmaps, `calcext:value-type` and zero `loext:tab-stop-distance`,
plus renumbers the automatic table styles. The full list against upstream, the provenance and the
licensing consequences live in the README's "Third-party code" section, with the license text in
`LICENSES/MPL-2.0.txt` — keep all three in step when you change the fork. Things worth knowing
before touching it:

- Keep it in upstream's style: no reformatting, retyping or tidying, so a diff against LibreOffice's
  version stays readable. It is excluded from ruff and mypy in `pyproject.toml` for exactly that
  reason; `cleanup.py` is the typed boundary in front of it.
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

## The LibreOffice extension

`extension/` is a Calc add-on. It **shells out to the `odslint` CLI** and reads `--format json`;
it imports nothing from this package. That is not laziness — LibreOffice's Python is the platform
interpreter, not the project venv, and `lxml` is a C extension that would have to match whatever
ABI a given LibreOffice build shipped. The JSON report is therefore a real API: changing its shape
breaks the extension.

To lint unsaved edits it `storeToURL`s the live document to a temp `.fods` with the
`OpenDocument Spreadsheet Flat XML` filter and lints that.

Four things cost an hour each to rediscover:

- **`odslint_core.py` must live in `python/pythonpath/`.** LibreOffice's component loader `exec`s
  the component with its own directory *off* `sys.path`, so a sibling import fails at registration.
  It fails silently, as an extension that installs and then does nothing. `pythonloader.py` adds a
  `pythonpath` directory beside the component automatically.
- **Dependencies go in the `lo:` namespace.** `OpenOffice.org-minimal-version` stopped at 4.x when
  LibreOffice forked, so asking it for 7.0 is unsatisfiable and `unopkg` refuses to install.
- **A pyuno IDL attribute is a Python attribute, not a getter.** `XToolPanel.Window` has to be set
  as `self.Window`; a `getWindow` method alone fails with "Property Window is unknown" the moment
  the sidebar deck opens.
- **`unopkg` blocks on a profile a running `soffice` holds.** Install before starting the office,
  or the test hangs with no output.

`tests/uno_smoke.py` drives all of this against a real LibreOffice — install, lint, highlight,
fix, undo, navigate, panel construction. It runs under the *system* Python (the venv cannot
`import uno`), so `tests/test_extension_uno.py` shells out to it and skips when there is no
LibreOffice. `tests/test_extension.py` covers `odslint_core` with ordinary pytest.

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

## Autofix

A rule may attach a `Fix` to a diagnostic. Rules are still pure — a `Fix` is a *description* of an
edit, and applying it is somebody else's job. That indirection is what lets one fix be applied two
ways: `fixer.py` rewrites the XML of a file, and the Calc extension replays the same `Edit` objects
through UNO against an open document.

`Applicability.SAFE` means the recalculated result cannot change (`--fix` applies these);
`UNSAFE` means it can (`--unsafe-fixes`). A rule that cannot express its fix unambiguously must
offer none — `data/number-stored-as-text` flags `1,234` but refuses to convert it, because that is
1234 in one locale and 1.234 in another.

Things that will bite you here:

- **An `Edit` carries a formula twice.** `formula` is the stored ODF form (`=SUM([.A4:.C4])`);
  `formula_a1` is Calc's own A1 convention (`=SUM(A4:C4)`). `XCell.setFormula` needs the second and
  does not reject the first — it silently stores a formula that evaluates to 0. `formula/edit.py:to_a1`
  is the conversion, and the only difference between the two spellings is reference qualification.
- **Editing one cell of a repeat means splitting the run.** `fixer._split_run` breaks a
  `number-columns-repeated` / `-rows-repeated` element into up to three so the change lands on one
  cell. Check any change here against `repeats_and_merges.fods` first.
- **lxml does not preserve newlines inside a start tag.** Re-serializing a file that has been through
  `odslint-clean` would collapse every tag it split, so `fixer._keeps_split_attributes` detects the
  cleaned layout and re-applies the same cosmetic pass. A file with mixed layout gets normalized to
  one or the other; that is accepted.
- **A cached `office:value` is left stale** after a formula fix. Calc recalculates on open (verified),
  and a missing value reads worse than a stale one in tools that never recalculate.

Still deferred: fixes that need to *add* document structure, such as `prefer-named-range` on a
constant with no name — that would have to write a `table:named-range` element, not just a cell.
