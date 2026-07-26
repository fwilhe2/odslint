"""Third-party code vendored verbatim, under its own license.

Nothing in here is written in odslint's style or checked by odslint's linters —
:mod:`flat_odf_cleanup` is kept byte-identical to upstream so it can be resynced
with a plain ``curl``, and it is excluded from ruff and mypy in ``pyproject.toml``
for that reason. Import it through :mod:`odslint.cleanup`, which wraps it in a
typed API, rather than reaching in here directly.

``flat_odf_cleanup.py``
    Mozilla Public License 2.0, from the LibreOffice project by way of
    https://github.com/fwilhe2/office-in-git/blob/main/scripts/flat-odf-cleanup.py
    (upstream: ``bin/flat-odf-cleanup.py`` in https://github.com/LibreOffice/core).
    The MPL is a per-file license: this file stays MPL-2.0 and carries its own
    notice; the rest of odslint remains MIT. Full license text is in
    ``LICENSES/MPL-2.0.txt``; provenance and how to resync are in the README's
    "Third-party code" section.
"""
