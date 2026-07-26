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
uv sync          # development
uv run odslint --help
```

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

## Scope

`odslint` reasons statically over the stored document. It does not recalculate
formulas, and it never writes to your files.

## License

MIT
