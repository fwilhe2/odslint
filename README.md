# odslint

A linter for LibreOffice Calc spreadsheets. It reads a `.ods` or flat `.fods`
file, builds a document model, runs rules over it, and reports findings anchored
to a sheet and cell — the spreadsheet equivalent of `ruff` or `eslint`.

Spreadsheets are software, but nothing checks them. `odslint` looks for the
things that make a sheet unmaintainable or quietly wrong: formulas built from
bare cell addresses, hardcoded constants, a fill that one cell breaks out of,
numbers that were imported as text.

```console
$ odslint budget.ods
budget.ods:Model!D1: warning [formula/prefer-named-range] references Model!B1 directly, but the named expression 'TaxRate' already covers exactly that range
    hint: replace [.$B$1] with TaxRate
budget.ods:Totals!D4: error [formula/inconsistent-in-range] formula '=SUM([.A4:.B4])' breaks the pattern of D2:D6 (4 of 5 cells share one shape)
    hint: the block otherwise reads like '=SUM([.A2:.C2])' in D2
2 problems (1 error, 1 warning)
```

## Install

Install from PyPI: https://pypi.org/project/odslint/

```console
# pip
pip install odslint
odslint budget.ods

# uv (install a command on your PATH)
uv tool install odslint
odslint budget.ods

# uv (one-off, nothing installed)
uvx odslint budget.ods

# venv
python -m venv .venv
source .venv/bin/activate
pip install odslint
odslint budget.ods
```

For development from a checkout:

```console
uv sync
uv run odslint --help
```

Both entry points, `odslint` and `odslint-clean`, come with the install.

## Rules

| Rule | Default | What it catches |
| --- | --- | --- |
| `formula/prefer-named-range` | warning | Addresses where a name exists, and absolute/cross-sheet references to constants that deserve one |
| `formula/inconsistent-in-range` | error | A formula that breaks the R1C1 pattern of the block around it — the classic copy-paste bug |
| `formula/magic-number` | warning | Numeric literals like `*1.19` that belong in a labelled input cell |
| `data/number-stored-as-text` | error | String cells that read as numbers or dates, which silently drop out of `SUM` |

`odslint --list-rules` prints the live list.

## Configuration

`.odslintrc.toml`, discovered upward from the file being linted:

```toml
[odslint]
fail-on = "warning"          # error | warning | info | never

[rules."formula/magic-number"]
severity = "info"            # or "off" to disable the rule
allowed = [0, 1, -1, 100]

[rules."formula/inconsistent-in-range"]
min_run = 4
```

Unknown rule ids and unknown option names are errors, so a typo fails loudly
instead of silently doing nothing.

## Suppressing a finding

A spreadsheet has no comment syntax, so directives live in **cell annotations**
(Insert > Comment). An annotation on the offending cell containing:

```
odslint-disable
odslint-disable formula/magic-number
odslint-disable formula/magic-number, data/number-stored-as-text
```

suppresses all, or the listed, rules for that cell. The rest of the annotation
is ignored, so the directive can sit next to a real note explaining *why*.

## Exit codes

`0` clean · `1` findings at or above `fail-on` · `2` tool error (unreadable
file, bad config).

## Keeping `.fods` files in git

A flat `.fods` is text, so it can live in version control like source — except
that LibreOffice rewrites a great deal on every save that has nothing to do with
what you changed. It renumbers its internal styles (`ce1` becomes `ce24`), emits
a default set of number formats nothing uses, re-declares three dozen namespaces
on every style element, bumps `meta:editing-cycles`, and re-renders cached
bitmaps for embedded charts. Edit one cell, commit a four-thousand-line diff.

`odslint-clean` strips all of that, in place:

```console
$ odslint-clean model.fods
cleaned model.fods

$ odslint-clean --check *.fods      # exit 1 if anything would change; writes nothing
```

It also reformats start tags to one attribute per line, so changing an attribute
shows up as a one-line diff. Running it twice changes nothing the second time,
which makes it usable as a pre-commit hook:

```yaml
- repo: local
  hooks:
    - id: odslint-clean
      name: normalize flat ODF
      entry: odslint-clean
      language: system
      files: \.fods$
```

Two caveats. This is **the one part of odslint that writes to your files**, and
it is lossy on purpose: unused styles, `office:settings`, `office:scripts`,
volatile metadata and cached replacement images are all dropped. That is right
for a document whose source of truth is git, and wrong for one you keep only as
a binary. It works on flat `.fods` only — a `.ods` package is a ZIP and has
nothing useful to diff.

The cleanup engine started life as LibreOffice's own `flat-odf-cleanup.py` and is
a fork of it, maintained here under the MPL-2.0 (see
[Third-party code](#third-party-code)); `odslint.cleanup` is a typed wrapper
around it.

## Scope

Linting reasons statically over the stored document. It does not recalculate
formulas, and it never writes to your files — `odslint-clean` is a separate
command precisely because it does.

## Third-party code

One file in this repository derives from another project and is not under this
project's license.

**`src/odslint/vendor/flat_odf_cleanup.py`**

| | |
| --- | --- |
| License | Mozilla Public License 2.0 — full text in [`LICENSES/MPL-2.0.txt`](LICENSES/MPL-2.0.txt) |
| Copyright | The LibreOffice contributors, per the notice in the file's header, plus this project's contributors for the changes below |
| Upstream | [`bin/flat-odf-cleanup.py`](https://github.com/LibreOffice/core/blob/11f10c48688436129337ffc7a082a56023c58071/bin/flat-odf-cleanup.py) in the LibreOffice core repository |
| Status | **Modified fork.** This repository is its canonical home — changes are made here, not resynced from elsewhere. It previously lived in [`fwilhe2/office-in-git`](https://github.com/fwilhe2/office-in-git/blob/main/scripts/flat-odf-cleanup.py); that copy is no longer the source of truth. |
| Used by | [`src/odslint/cleanup.py`](src/odslint/cleanup.py), which drives it |

The file keeps LibreOffice's header and MPL notice, and stays MPL-2.0 — the MPL
is a per-file copyleft, so the modifications are published under the same
license, while the rest of odslint is MIT.

### How the fork differs from LibreOffice's script

Upstream is a one-shot `infile outfile` script that prints its every decision to
stdout. The fork keeps its cleanup passes intact and adds:

- **Quiet by default.** Every `print` became `log()`, gated on a module-global
  `VERBOSE`, so the module can be imported without spraying stdout.
- **In-place, multi-file entry point.** `flat-odf-cleanup.py [--verbose] a.fods
  b.fods …` cleans each file where it sits and only rewrites when the bytes
  actually change, instead of always writing a second file.
- **One attribute per line.** `split_attributes_onto_lines` reformats any start
  tag with two or more attributes, so changing one attribute is a one-line diff
  rather than a rewritten mega-line. It re-parses its own output and asserts the
  tree is unchanged.
- **Namespace pruning that survives formulas.** `remove_unused_namespaces` drops
  the ~35 declarations LibreOffice re-emits on every element. lxml's own
  `cleanup_namespaces` only sees *structural* use, and would strip `xmlns:of` —
  which `table:formula="of:=SUM(…)"` references as plain text, leaving
  LibreOffice with `Err:510`. Prefixes that appear textually are kept.
- **Unused number-format styles removed** (upstream's `TODO 3 other styles`).
  LibreOffice writes a default set of `N0`, `N2`, … whether or not a cell uses
  one. `style:map` references are chased to a fixed point first.
- **Volatile `office:meta` children removed** — `dc:date`, `meta:editing-cycles`,
  `meta:editing-duration`, `meta:generator`, `meta:document-statistic` — and the
  `office:meta` element itself when that leaves it empty.
- **Cached OLE replacement images removed** (upstream's `TODO: replace embedded
  image with some tiny one`). A chart or embedded object ships both its native
  representation and a rendered base64 bitmap that churns on every save;
  LibreOffice re-renders from the native one, and PDF export is unchanged.
- **`calcext:value-type` removed.** A LibreOffice extension attribute derived
  from the cell's number format and recomputed on load.
- **Zero-length `loext:tab-stop-distance` removed.** LibreOffice adds
  `="0cm"` on save even when the source had none; non-zero values are kept.
- **Automatic table styles renumbered** to a dense, document-order sequence per
  family (`ce1…`, `co1…`, `ro1…`, `ta1…`), so LibreOffice's internal counter
  shifting `ce1` to `ce24` stops producing noise diffs. It bails out rather than
  rename into a collision.

It is still excluded from `ruff` and `mypy` in `pyproject.toml`: it is upstream's
code in upstream's style, and keeping it that way is what makes a diff against
LibreOffice's version readable. Keep new work in that style too, and let
[`src/odslint/cleanup.py`](src/odslint/cleanup.py) be the typed boundary in front
of it. To see the full delta:

```console
$ curl -o /tmp/upstream.py \
    https://raw.githubusercontent.com/LibreOffice/core/master/bin/flat-odf-cleanup.py
$ diff -u /tmp/upstream.py src/odslint/vendor/flat_odf_cleanup.py
$ uv run pytest tests/test_cleanup.py
```

Nothing else here is third-party. The only runtime dependency is `lxml`
(BSD-3-Clause), which is installed normally rather than vendored.

## License

MIT — see [`LICENSE`](LICENSE). The one exception is the forked file described
under [Third-party code](#third-party-code), which remains under the MPL-2.0.
The MPL and the MIT license are compatible; distributing the two together, as
this project does, is exactly the case the MPL's file-scoped copyleft is
designed for.
