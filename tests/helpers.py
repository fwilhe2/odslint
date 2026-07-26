"""Test helpers: building fixtures and running single rules.

Fixtures are flat ``.fods`` on purpose — they are text, so they diff and review
like code. :func:`fods_to_ods` repackages one into a real ZIP so the package
loader gets exercised by the same fixtures rather than by checked-in binaries.
"""

from __future__ import annotations

import zipfile
from copy import deepcopy
from pathlib import Path

from lxml import etree

from odslint.config import Config
from odslint.diagnostics import Diagnostic
from odslint.engine import lint_document, select_rules
from odslint.loader import NS, load
from odslint.model import Document

MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"

FIXTURES = Path(__file__).parent / "fixtures"

MANIFEST = f"""<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
                   manifest:version="1.2">
  <manifest:file-entry manifest:full-path="/" manifest:media-type="{MIMETYPE}"/>
  <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>
"""


def fixture(name: str) -> Path:
    path = FIXTURES / name
    assert path.is_file(), f"missing fixture: {path}"
    return path


def fods_to_ods(source: Path, dest: Path) -> Path:
    """Repackage a flat fixture as a real ODS ZIP.

    The ``mimetype`` entry must come first and be stored uncompressed, or
    LibreOffice refuses the file — so the helper that produces our test packages
    is also the place that documents the rule.
    """
    root = etree.parse(str(source)).getroot()
    content = etree.Element(f"{{{NS['office']}}}document-content", nsmap=root.nsmap)
    content.set(f"{{{NS['office']}}}version", "1.3")
    for tag in ("automatic-styles", "body"):
        element = root.find(f"{{{NS['office']}}}{tag}")
        if element is not None:
            content.append(deepcopy(element))

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, MIMETYPE)
        zf.writestr("META-INF/manifest.xml", MANIFEST)
        zf.writestr(
            "content.xml",
            etree.tostring(content, xml_declaration=True, encoding="UTF-8"),
        )
    return dest


# -- building documents from a compact spec --------------------------------

_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<office:document
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    xmlns:of="{of}"
    xmlns:calcext="{calcext}"
    office:version="1.3"
    office:mimetype="{mimetype}">
  <office:body>
    <office:spreadsheet>
""".format(calcext=NS["calcext"], of=NS["of"], mimetype=MIMETYPE)

_FOOTER = """    </office:spreadsheet>
  </office:body>
</office:document>
"""


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def txt(value: str) -> str:
    """A string cell."""
    return (
        f'<table:table-cell office:value-type="string" '
        f'office:string-value="{_escape(value)}"><text:p>{_escape(value)}</text:p>'
        f"</table:table-cell>"
    )


def num(value: float) -> str:
    """A numeric cell."""
    return (
        f'<table:table-cell office:value-type="float" office:value="{value}">'
        f"<text:p>{value}</text:p></table:table-cell>"
    )


def formula(expression: str, cached: float = 0) -> str:
    """A formula cell. ``expression`` is written as the user would type it."""
    body = expression if expression.startswith("=") else f"={expression}"
    return (
        f'<table:table-cell table:formula="of:{_escape(body)}" '
        f'office:value-type="float" office:value="{cached}">'
        f"<text:p>{cached}</text:p></table:table-cell>"
    )


EMPTY = "<table:table-cell/>"


def write_fods(
    path: Path,
    sheets: dict[str, list[list[str]]],
    named_ranges: dict[str, str] | None = None,
) -> Path:
    """Write a minimal flat spreadsheet built from cell-XML snippets."""
    parts = [_HEADER]
    for name, rows in sheets.items():
        parts.append(f'      <table:table table:name="{_escape(name)}">\n')
        for row in rows:
            parts.append("        <table:table-row>")
            parts.extend(row)
            parts.append("</table:table-row>\n")
        parts.append("      </table:table>\n")

    if named_ranges:
        parts.append("      <table:named-expressions>\n")
        for name, address in named_ranges.items():
            parts.append(
                f'        <table:named-range table:name="{_escape(name)}" '
                f'table:cell-range-address="{_escape(address)}"/>\n'
            )
        parts.append("      </table:named-expressions>\n")

    parts.append(_FOOTER)
    path.write_text("".join(parts), encoding="utf-8")
    return path


def build(tmp_path: Path, sheets: dict[str, list[list[str]]], **kwargs: object) -> Document:
    """Write a spec to a temp ``.fods`` and load it."""
    target = tmp_path / "book.fods"
    write_fods(target, sheets, **kwargs)  # type: ignore[arg-type]
    return load(target)


# -- running rules ---------------------------------------------------------


def run_rule(doc: Document, rule_id: str, **options: object) -> list[Diagnostic]:
    """Run exactly one rule against a document."""
    config = Config()
    rules = select_rules(config, [rule_id])
    assert rules, f"no such rule: {rule_id}"
    if options:
        rules[0].options.update(options)
    return lint_document(doc, config, rules)


def cells(diagnostics: list[Diagnostic]) -> list[str]:
    """Diagnostic locations, for compact assertions."""
    return [d.location for d in diagnostics]
