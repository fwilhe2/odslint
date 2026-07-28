"""Drive the extension's UNO half against a real LibreOffice.

Run as a standalone script by the *system* Python, not by the project venv:
``uno`` is only importable from the interpreter LibreOffice was built against,
which is exactly the constraint the extension itself lives under. The pytest
wrapper in ``test_extension_uno.py`` shells out to here and reads the report.

Prints one ``ok:``/``FAIL:`` line per check and exits non-zero if any failed.
"""

import os
import subprocess
import sys
import tempfile
import time

_EXTENSION = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "extension", "python")
# LibreOffice puts "pythonpath" on sys.path for us and loads the component by
# URL; importing both directly is this script standing in for that.
sys.path.insert(0, os.path.join(_EXTENSION, "pythonpath"))
sys.path.insert(0, _EXTENSION)

import uno  # noqa: E402
import unohelper  # noqa: E402
from com.sun.star.beans import PropertyValue  # noqa: E402

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    print(("ok:   " if condition else "FAIL: ") + name + ((" — " + str(detail)) if detail else ""))


def pv(name, value):
    prop = PropertyValue()
    prop.Name = name
    prop.Value = value
    return prop


def connect(port, tries=60):
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local
    )
    for _ in range(tries):
        try:
            return resolver.resolve(
                f"uno:socket,host=localhost,port={port};urp;StarOffice.ComponentContext"
            )
        except Exception:
            time.sleep(1)
    return None


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    workdir = tempfile.mkdtemp(prefix="odslint-uno-")
    profile = os.path.join(workdir, "profile")
    port = 2099

    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env["PATH"] = "/usr/bin:/bin"

    # -- the .oxt has to install before anything else is worth testing
    sys.path.insert(0, os.path.join(root, "tools"))
    import build_oxt

    oxt = os.path.join(workdir, "odslint.oxt")
    build_oxt.build(__import__("pathlib").Path(oxt))

    installed = subprocess.run(
        [
            "unopkg",
            "add",
            "--force",
            "-env:UserInstallation=file://" + profile,
            oxt,
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    check(
        "unopkg installs the extension",
        installed.returncode == 0,
        installed.stdout.decode("utf-8", "replace").strip()[:400],
    )

    listed = subprocess.run(
        ["unopkg", "list", "-env:UserInstallation=file://" + profile],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    check(
        "the extension is registered",
        b"de.fwilhe2.odslint" in listed.stdout,
        listed.stdout.decode("utf-8", "replace").strip()[:400],
    )

    proc = subprocess.Popen(
        [
            "soffice",
            "-env:UserInstallation=file://" + profile,
            "--headless",
            "--norestore",
            f"--accept=socket,host=localhost,port={port};urp;",
        ],
        env=env,
    )
    try:
        ctx = connect(port)
        if ctx is None:
            check("connect to LibreOffice", False, "no bridge")
            return 1
        check("connect to LibreOffice", True)

        import odslint_core as core
        import odslint_ext as ext

        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        source = os.path.join(root, "tests", "fixtures", "inconsistent_formulas.fods")
        document = desktop.loadComponentFromURL(
            unohelper.systemPathToFileUrl(source), "_blank", 0, (pv("Hidden", True),)
        )

        handler = ext.OdslintHandler(ctx)

        # -- storeToURL + CLI: the lint pipeline, including unsaved edits
        settings = dict(core.DEFAULT_SETTINGS)
        settings["interpreter"] = os.path.join(root, ".venv", "bin", "odslint")
        settings["unsafe_fixes"] = True
        findings = handler._lint(document, settings)
        check("linting the live document returns findings", len(findings) == 1, findings)
        check(
            "the finding is anchored to D4",
            findings and findings[0].location == "Totals!D4",
            findings[0].location if findings else "",
        )

        # -- an edit made in memory is visible to the next lint, unsaved
        sheet = document.Sheets.getByIndex(0)
        sheet.getCellByPosition(3, 5).setFormula("=SUM(A6:B6)")
        findings_after = handler._lint(document, settings)
        check(
            "an unsaved edit is linted too",
            len(findings_after) == 2,
            [f.location for f in findings_after],
        )
        sheet.getCellByPosition(3, 5).setFormula("=SUM(A6:C6)")

        # -- highlighting is exactly reversible
        highlighter = ext.Highlighter()
        cell = sheet.getCellByPosition(3, 3)
        original = cell.CellBackColor
        document.setModified(False)
        highlighter.apply(document, findings)
        check(
            "highlight tints the flagged cell", cell.CellBackColor != original, cell.CellBackColor
        )
        check("highlighting does not dirty a clean document", not document.isModified())
        highlighter.clear(document)
        check("clearing restores the original colour", cell.CellBackColor == original)

        # -- applying a fix through UNO
        before = sheet.getCellByPosition(3, 3).getFormula()
        edits = core.collect_edits(findings, unsafe=True)
        check("there is an edit to apply", len(edits) == 1, edits)
        applied = handler.apply_edits(document, edits)
        after = sheet.getCellByPosition(3, 3).getFormula()
        check("apply_edits reports one applied", applied == 1)
        check(
            "the formula was rewritten to the majority shape",
            after == "=SUM(A4:C4)",
            f"{before!r} -> {after!r}",
        )
        check(
            "the fixed cell recalculates",
            sheet.getCellByPosition(3, 3).getValue() == 24.0,
            sheet.getCellByPosition(3, 3).getValue(),
        )

        # -- and the fix actually resolved the finding
        check("re-linting after the fix is clean", handler._lint(document, settings) == [])

        # -- undo reverts the whole batch in one step
        try:
            document.getUndoManager().undo()
            check(
                "one undo reverts the fix",
                sheet.getCellByPosition(3, 3).getFormula() == before,
                sheet.getCellByPosition(3, 3).getFormula(),
            )
        except Exception as exc:
            check("one undo reverts the fix", False, exc)

        # -- the sidebar panel factory is reachable and builds a window
        try:
            factory = ctx.ServiceManager.createInstanceWithContext(
                "de.fwilhe2.odslint.PanelFactory", ctx
            )
            check("the sidebar panel factory is registered", factory is not None)
        except Exception as exc:
            check("the sidebar panel factory is registered", False, exc)
            factory = None

        if factory is not None:
            try:
                frame = document.getCurrentController().getFrame()
                toolkit = ctx.ServiceManager.createInstanceWithContext(
                    "com.sun.star.awt.Toolkit", ctx
                )
                descriptor = uno.createUnoStruct("com.sun.star.awt.WindowDescriptor")
                descriptor.Type = uno.Enum("com.sun.star.awt.WindowClass", "TOP")
                descriptor.WindowServiceName = "window"
                descriptor.ParentIndex = -1
                descriptor.Bounds = uno.createUnoStruct("com.sun.star.awt.Rectangle")
                descriptor.Bounds.Width = 320
                descriptor.Bounds.Height = 400
                parent = toolkit.createWindow(descriptor)
                element = factory.createUIElement(
                    "private:resource/toolpanel/OdslintPanelFactory/OdslintPanel",
                    (pv("Frame", frame), pv("ParentWindow", parent)),
                )
                check("the panel factory returns a UI element", element is not None)
                panel = element.getRealInterface()
                check("the element exposes a tool panel", panel is not None)
                # Across the bridge an IDL attribute surfaces as a property,
                # so XToolPanel.Window is the spelling a real caller uses.
                window = getattr(panel, "Window", None)
                if window is None:
                    window = panel.getWindow()
                check("the tool panel has a window", window is not None)
                element.dispose()
            except Exception as exc:
                check("the panel factory builds a window", False, exc)

        # -- navigation selects the right cell
        try:
            handler.go_to(document, findings[0])
            selected = document.getCurrentController().getSelection()
            check(
                "navigating selects the flagged cell",
                selected.AbsoluteName.endswith("$D$4"),
                selected.AbsoluteName,
            )
        except Exception as exc:
            check("navigating selects the flagged cell", False, exc)

        document.close(False)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS)} checks, {len(failed)} failed")
    return 1 if failed else 0


sys.exit(main())
