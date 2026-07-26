"""Code from another project, under its own license.

Nothing in here is written in odslint's style or checked by odslint's linters:
:mod:`flat_odf_cleanup` is a fork of an upstream script and is deliberately kept
in upstream's style, so that a diff against LibreOffice's version stays readable.
It is excluded from ruff and mypy in ``pyproject.toml`` for that reason. Import it
through :mod:`odslint.cleanup`, which wraps it in a typed API, rather than
reaching in here directly.

``flat_odf_cleanup.py``
    Mozilla Public License 2.0, forked from the LibreOffice project's
    ``bin/flat-odf-cleanup.py`` (https://github.com/LibreOffice/core). **This
    repository is the canonical home of the fork** — changes are made here, not
    resynced from anywhere else; it previously lived in
    https://github.com/fwilhe2/office-in-git, which is no longer the source of
    truth. The MPL is a per-file license: this file stays MPL-2.0 and carries
    LibreOffice's own notice, and the modifications go out under the same
    license, while the rest of odslint remains MIT. Full license text is in
    ``LICENSES/MPL-2.0.txt``; provenance and the list of changes against
    LibreOffice's version are in the README's "Third-party code" section.
"""
