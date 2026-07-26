"""Round-trip every fixture through LibreOffice and re-lint it.

Hand-written fixtures can be subtly unlike what Calc actually writes — the first
version of these fixtures omitted ``xmlns:of``, which LibreOffice "fixed" by
emitting ``of:=of:=SUM(...)``. Only a real round-trip catches that class of bug,
so it is worth the couple of seconds.

Skipped automatically when LibreOffice is not installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from helpers import FIXTURES, fixture
from odslint.cleanup import clean_file
from odslint.config import Config
from odslint.engine import lint_file, select_rules
from odslint.loader import load

SOFFICE = shutil.which("soffice") or shutil.which("libreoffice")

pytestmark = pytest.mark.skipif(SOFFICE is None, reason="LibreOffice is not installed")

FIXTURE_NAMES = sorted(p.name for p in FIXTURES.glob("*.fods"))


def _soffice_env() -> dict[str, str]:
    """Environment for LibreOffice, with our virtualenv taken off ``PATH``.

    LibreOffice's Python script provider resolves ``python3`` from ``PATH``.
    When that lands on the project venv it fails to load any document with an
    ``office:annotation`` in it, reporting only "source file could not be
    loaded" — which looks exactly like a corrupt fixture.
    """
    env = dict(os.environ)
    venv = env.get("VIRTUAL_ENV")
    if venv:
        env["PATH"] = os.pathsep.join(
            entry for entry in env.get("PATH", "").split(os.pathsep) if not entry.startswith(venv)
        )
    return env


def _convert(tmp_path_factory, sources):
    """Run every source through ``soffice --convert-to ods`` into a fresh dir."""
    out = tmp_path_factory.mktemp("libreoffice")
    profile = tmp_path_factory.mktemp("profile")

    result = subprocess.run(
        [
            SOFFICE,
            f"-env:UserInstallation=file://{profile}",
            "--headless",
            "--convert-to",
            "ods",
            "--outdir",
            str(out),
            *[str(source) for source in sources],
        ],
        capture_output=True,
        timeout=300,
        text=True,
        env=_soffice_env(),
    )

    missing = [
        name for name in FIXTURE_NAMES if not (out / f"{name.removesuffix('.fods')}.ods").exists()
    ]
    if missing:
        pytest.fail(f"LibreOffice did not convert {missing}: {result.stderr.strip()}")
    return out


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    """Every ``.fods`` fixture as written by LibreOffice itself."""
    return _convert(tmp_path_factory, [fixture(name) for name in FIXTURE_NAMES])


@pytest.fixture(scope="module")
def converted_after_cleanup(tmp_path_factory):
    """Every fixture run through ``odslint-clean`` and then through LibreOffice."""
    staged = tmp_path_factory.mktemp("cleaned")
    sources = [shutil.copy(fixture(name), staged / name) for name in FIXTURE_NAMES]
    for source in sources:
        clean_file(source)
    return _convert(tmp_path_factory, sources)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_model_survives_a_libreoffice_round_trip(converted, name):
    flat = load(fixture(name))
    real = load(converted / f"{name.removesuffix('.fods')}.ods")

    assert [s.name for s in real.sheets] == [s.name for s in flat.sheets]
    for expected, actual in zip(flat.sheets, real.sheets, strict=True):
        assert actual.hidden == expected.hidden
        assert sorted(actual.cells) == sorted(expected.cells), f"{name}: cell grid moved"
        for key, cell in expected.cells.items():
            other = actual.cells[key]
            assert other.formula == cell.formula
            assert other.text == cell.text
            assert other.annotations == cell.annotations
            assert other.cols_spanned == cell.cols_spanned


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_diagnostics_are_identical_for_both_packagings(converted, name):
    config = Config()
    rules = select_rules(config)

    def signature(path):
        return [(d.rule_id, d.location, d.message) for d in lint_file(path, config, rules)]

    assert signature(converted / f"{name.removesuffix('.fods')}.ods") == signature(fixture(name))


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_a_cleaned_fixture_still_opens_in_libreoffice(converted_after_cleanup, name):
    """The cleanup strips namespace declarations and renames styles.

    Both are the kind of edit that a static reader shrugs at and LibreOffice
    chokes on — dropping ``xmlns:of`` turns every formula into ``Err:510``, and
    the linter would never notice because it resolves prefixes structurally.
    Getting Calc itself to reopen the cleaned file is the only real proof.
    """
    config = Config()
    rules = select_rules(config)

    def signature(path):
        return [(d.rule_id, d.location, d.message) for d in lint_file(path, config, rules)]

    reopened = converted_after_cleanup / f"{name.removesuffix('.fods')}.ods"
    assert signature(reopened) == signature(fixture(name))
