"""Rewriting one part of an ``.ods`` package without disturbing the rest.

An ``.ods`` is a ZIP, and it holds far more than this linter understands:
``styles.xml``, ``settings.xml``, embedded images, chart objects, digital
signatures, whatever a future LibreOffice adds. A fixer that rebuilt the package
from its model would silently throw all of that away, so this module copies every
entry it did not set out to change straight through, in its original order and
with its original compression method.

The one rule that is not negotiable: ``mimetype`` comes first and is ``STORED``.
An ``.ods`` whose first entry is deflated is rejected by LibreOffice outright.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path

MIMETYPE = "mimetype"
CONTENT = "content.xml"


class PackageError(Exception):
    """The file is not a usable ODF package."""


def is_package(path: Path) -> bool:
    """Whether this is a ZIP-packaged ``.ods`` rather than flat XML.

    Detected by content, not extension, exactly as the loader does it.
    """
    return zipfile.is_zipfile(path)


def read_part(path: Path, name: str = CONTENT) -> bytes:
    try:
        with zipfile.ZipFile(path) as zf:
            return zf.read(name)
    except KeyError as exc:
        raise PackageError(f"{path}: no {name} in the package") from exc
    except zipfile.BadZipFile as exc:
        raise PackageError(f"{path}: not a readable ZIP: {exc}") from exc


def replace_part(path: Path, data: bytes, name: str = CONTENT) -> None:
    """Rewrite one entry of the package in place, preserving every other entry.

    The write goes to a temporary file next to the target and is moved into
    place with :func:`os.replace`, so an interrupted run leaves the original
    intact rather than a half-written package.
    """
    tmp = path.with_name(f".{path.name}.odslint-tmp")
    try:
        with zipfile.ZipFile(path) as source:
            if name not in source.namelist():
                raise PackageError(f"{path}: no {name} in the package")
            with zipfile.ZipFile(tmp, "w") as target:
                for info in _ordered(source):
                    payload = data if info.filename == name else source.read(info.filename)
                    target.writestr(_copy_info(info), payload)
        shutil.copystat(path, tmp)
        os.replace(tmp, path)
    except zipfile.BadZipFile as exc:
        tmp.unlink(missing_ok=True)
        raise PackageError(f"{path}: not a readable ZIP: {exc}") from exc
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _ordered(source: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Entries with ``mimetype`` hoisted to the front, everything else in order."""
    infos = source.infolist()
    mimetype = [i for i in infos if i.filename == MIMETYPE]
    return mimetype + [i for i in infos if i.filename != MIMETYPE]


def _copy_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    """A fresh header carrying over everything the writer will not recompute."""
    out = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    # STORED for mimetype is what makes the package recognizable; for every
    # other entry, keep whatever the producer chose.
    out.compress_type = zipfile.ZIP_STORED if info.filename == MIMETYPE else info.compress_type
    out.external_attr = info.external_attr
    out.internal_attr = info.internal_attr
    out.create_system = info.create_system
    out.comment = info.comment
    return out
