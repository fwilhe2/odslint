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

```console
uv tool install git+https://github.com/fwilhe2/odslint      # odslint on your PATH
uvx --from git+https://github.com/fwilhe2/odslint odslint   # one-off, nothing installed

uv sync          # development
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

The cleanup itself is not ours — it is LibreOffice's own `flat-odf-cleanup.py`,
vendored under the MPL-2.0 (see [Third-party code](#third-party-code));
`odslint.cleanup` is a typed wrapper around it.

## Scope

Linting reasons statically over the stored document. It does not recalculate
formulas, and it never writes to your files — `odslint-clean` is a separate
command precisely because it does.

## Third-party code

One file in this repository was not written for it and is not under its license.

**`src/odslint/vendor/flat_odf_cleanup.py`**

| | |
| --- | --- |
| License | Mozilla Public License 2.0 — full text in [`LICENSES/MPL-2.0.txt`](LICENSES/MPL-2.0.txt) |
| Copyright | The LibreOffice contributors, per the notice in the file's header |
| Origin | [`bin/flat-odf-cleanup.py`](https://github.com/LibreOffice/core/blob/11f10c48688436129337ffc7a082a56023c58071/bin/flat-odf-cleanup.py) in the LibreOffice core repository |
| Taken from | [`scripts/flat-odf-cleanup.py`](https://github.com/fwilhe2/office-in-git/blob/main/scripts/flat-odf-cleanup.py) in `fwilhe2/office-in-git`, at commit `46c8770` (2026-07-21), which carries LibreOffice's script plus several additions |
| Modifications | None. Byte-identical to the source above, header and license notice intact. |
| Used by | [`src/odslint/cleanup.py`](src/odslint/cleanup.py), which drives it |

It is kept verbatim so it can be resynced with a plain `curl`, which is also why
it is excluded from `ruff` and `mypy` in `pyproject.toml`:

```console
$ curl -o src/odslint/vendor/flat_odf_cleanup.py \
    https://raw.githubusercontent.com/fwilhe2/office-in-git/main/scripts/flat-odf-cleanup.py
$ uv run pytest tests/test_cleanup.py
```

If you ever do need to change it, the MPL is a per-file copyleft: the modified
file stays MPL-2.0 and the change has to be published under the same license.
Prefer sending the fix upstream and re-vendoring.

Nothing else here is third-party. The only runtime dependency is `lxml`
(BSD-3-Clause), which is installed normally rather than vendored.

## License

MIT — see [`LICENSE`](LICENSE). The one exception is the vendored file described
under [Third-party code](#third-party-code), which remains under the MPL-2.0.
The MPL and the MIT license are compatible; distributing the two together, as
this project does, is exactly the case the MPL's file-scoped copyleft is
designed for.
