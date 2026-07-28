#!/usr/bin/env python3
"""Package ``extension/`` into an installable ``.oxt``.

An ``.oxt`` is a ZIP with a prescribed layout, so this is deliberately a plain
script with no build-system entanglement: run it, get ``dist/odslint-VERSION.oxt``,
install it with ``unopkg add``.

The version is read from the package rather than duplicated, and written into
``description.xml`` on the way in, so the extension manager and ``odslint
--version`` can never disagree.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXTENSION = ROOT / "extension"
#: Not ``dist/``: ``uv publish`` uploads everything it finds there, and an .oxt
#: is not a Python distribution.
DIST = ROOT / "dist-oxt"


def version() -> str:
    text = (ROOT / "src" / "odslint" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if match is None:
        raise SystemExit("could not find __version__ in src/odslint/__init__.py")
    return match.group(1)


def build(target: Path | None = None) -> Path:
    release = version()
    DIST.mkdir(exist_ok=True)
    target = target or DIST / f"odslint-{release}.oxt"

    files = sorted(
        p
        for p in EXTENSION.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )
    if not files:
        raise SystemExit(f"nothing to package in {EXTENSION}")

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as oxt:
        for path in files:
            arcname = str(path.relative_to(EXTENSION))
            data = path.read_bytes()
            if arcname == "description.xml":
                data = re.sub(
                    rb'(<version value=")[^"]*(")',
                    rb"\g<1>" + release.encode() + rb"\g<2>",
                    data,
                )
            oxt.writestr(arcname, data)

    return target


if __name__ == "__main__":
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if destination is not None and destination.is_dir():
        destination = destination / f"odslint-{version()}.oxt"
    built = build(destination)
    print(f"{built}  ({built.stat().st_size} bytes)")
    print(f"install with:  unopkg add --force {built}")
    sys.exit(0)
