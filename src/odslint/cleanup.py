"""Normalize a flat ``.fods`` file so it diffs like source code.

LibreOffice rewrites a great deal on every save that has nothing to do with what
changed: it renumbers its automatic styles (``ce1`` becomes ``ce24``), emits a
default set of number formats whether or not a cell uses one, re-declares ~35
namespaces on every ``style:style``, refreshes ``meta:editing-cycles`` and the
generator string, and re-renders cached bitmaps for embedded objects. Committing
a ``.fods`` without cleaning it first produces enormous diffs for a one-cell
edit, which is exactly what the "fixtures are text, never checked-in binaries"
convention is trying to avoid.

The cleanup engine is :mod:`odslint.vendor.flat_odf_cleanup`, a fork of
LibreOffice's ``bin/flat-odf-cleanup.py`` under the MPL-2.0 whose canonical home
is this repository (see that package's docstring, and the README for what the
fork adds). This module is the typed wrapper around it.

Two things to know before pointing it at a document you care about:

* It is **the one part of odslint that writes to your files.** Everything else
  reasons statically over the stored document and never touches it. Nothing in
  the lint path calls into here.
* It is lossy by design. Unused styles, ``office:settings``, ``office:scripts``,
  volatile ``office:meta`` children and cached OLE replacement images are all
  dropped. That is fine for a file whose source of truth is git; it is not a
  general-purpose "optimize my spreadsheet" pass. Dropping ``office:settings``
  in particular means :attr:`Document.settings <odslint.model.Document.settings>`
  comes back empty on the cleaned file.
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

from lxml import etree

from odslint import __version__
from odslint.vendor import flat_odf_cleanup as _vendor

EXIT_OK = 0
EXIT_WOULD_CHANGE = 1
EXIT_ERROR = 2


class CleanupError(Exception):
    """The file could not be cleaned."""


def clean_bytes(data: bytes, *, verbose: bool = False) -> bytes:
    """Return the cleaned form of a flat ODF document.

    Pure: takes and returns bytes, touches no files.
    """
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise CleanupError(f"malformed XML: {exc}") from exc

    if root.tag != "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}document":
        raise CleanupError(f"root element is {root.tag}, expected office:document")

    # The forked script is a script: it reads ``VERBOSE`` and ``root`` as
    # module globals rather than taking them as arguments (``collect_all_attribute``
    # closes over the ``root`` its ``__main__`` block sets). Priming both here is
    # the price of keeping it shaped like upstream. Not reentrant, which is
    # fine — cleaning is a single-threaded CLI operation.
    previous_verbose = _vendor.VERBOSE
    _vendor.VERBOSE = verbose
    _vendor.root = root
    try:
        _vendor.remove_unused(root)  # type: ignore[no-untyped-call]
        serialized: bytes = etree.tostring(root, encoding="UTF-8", xml_declaration=False)
        formatted: bytes = _vendor.split_attributes_onto_lines(  # type: ignore[no-untyped-call]
            serialized
        )
    finally:
        _vendor.VERBOSE = previous_verbose

    # Attribute splitting is cosmetic; upstream proves that per file by
    # re-parsing, and so do we. lxml normalizes intra-tag whitespace away, so
    # comparing the two single-line serializations is an exact structural check.
    if etree.tostring(etree.fromstring(formatted)) != etree.tostring(etree.fromstring(serialized)):
        raise CleanupError("attribute reformatting changed the document structure")

    return assemble(root, formatted)


def assemble(root: etree._Element, body: bytes) -> bytes:
    """Wrap the serialized root back up into a whole document.

    Upstream serializes the root element alone, which quietly drops any comment
    or processing instruction sitting outside it — our fixtures open with a
    comment saying what they test, and a cleanup pass that eats the
    documentation is not one anybody would run. lxml can serialize the whole
    tree instead, but it drops the newline after a leading comment, which then
    leaves the root's start tag mid-line where the attribute splitter can't see
    it. So put the pieces together here, with the newlines a text file wants:
    a trailing one at the end of the file, and a hand-written declaration using
    the double quotes every other ODF writer uses (lxml prefers single).
    """
    before = [etree.tostring(node, with_tail=False) for node in _preceding(root)]
    after = [etree.tostring(node, with_tail=False) for node in root.itersiblings()]
    parts = [b'<?xml version="1.0" encoding="UTF-8"?>', *before, body, *after]
    return b"\n".join(parts) + b"\n"


def _preceding(root: etree._Element) -> list[etree._Element]:
    return list(reversed(list(root.itersiblings(preceding=True))))


def clean_file(path: str | Path, *, check: bool = False, verbose: bool = False) -> bool:
    """Clean ``path`` in place. Returns whether the file changed.

    With ``check=True`` nothing is written and the return value says whether it
    *would* have changed.
    """
    path = Path(path)
    if not path.is_file():
        raise CleanupError(f"not a file: {path}")
    if zipfile.is_zipfile(path):
        raise CleanupError(
            f"{path}: this is an .ods package, not flat XML — "
            "save it as .fods (Flat XML ODF Spreadsheet) first"
        )

    original = path.read_bytes()
    cleaned = clean_bytes(original, verbose=verbose)
    if cleaned == original:
        return False
    if not check:
        path.write_bytes(cleaned)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="odslint-clean",
        description=(
            "Normalize flat .fods files in place so they diff like source code. "
            "This rewrites the given files and drops unused styles, settings, "
            "scripts and volatile metadata."
        ),
    )
    parser.add_argument("paths", nargs="*", type=Path, help="flat .fods files to clean")
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if any file would change",
    )
    parser.add_argument("--verbose", action="store_true", help="report what is removed")
    parser.add_argument("--version", action="version", version=f"odslint {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """``odslint-clean`` entry point.

    Exit codes mirror the linter's: ``0`` success, ``1`` a file would change
    under ``--check``, ``2`` tool error. Writing a file is success, not a
    finding — only ``--check`` reports drift.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.paths:
        parser.error("no files given")

    changed = False
    failed = False
    for path in args.paths:
        try:
            if clean_file(path, check=args.check, verbose=args.verbose):
                changed = True
                print(f"{'would clean' if args.check else 'cleaned'} {path}")
            elif args.verbose:
                print(f"unchanged {path}")
        except CleanupError as exc:
            print(f"odslint-clean: {exc}", file=sys.stderr)
            failed = True

    if failed:
        return EXIT_ERROR
    return EXIT_WOULD_CHANGE if (changed and args.check) else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
