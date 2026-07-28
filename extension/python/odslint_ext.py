"""odslint inside LibreOffice Calc: the UNO half.

Registers a protocol handler so the Tools menu entries dispatch here. The flow
for a lint is:

1. ``storeToURL`` the *live* document to a temporary flat ``.fods``. Linting the
   file on disk would miss everything the user has typed since their last save,
   which is exactly the feedback an editor integration exists to give.
2. Run the ``odslint`` CLI on it and read the JSON report. The extension never
   imports odslint: LibreOffice's Python is the platform interpreter rather than
   the project's virtualenv, and ``lxml`` is a C extension that would have to
   match whatever ABI this particular LibreOffice build shipped.
3. Show the findings, navigate to cells, and — when asked — apply the fixes the
   report carries, through UNO so they land in Calc's own undo stack.

Highlighting is deliberately off the document's critical path: the original cell
background of every cell it touches is remembered, put back on request, and
stripped before a save, so linting can never leave a mark in the user's file.

``odslint_core`` lives in ``python/pythonpath/`` rather than beside this file.
That is not a preference: LibreOffice's component loader ``exec``s this module
under the name ``uno_component`` with its own directory *not* on ``sys.path``, so
a plain sibling import fails at registration time — and it fails silently, as an
extension that installs and then does nothing. ``pythonloader.py`` adds a
``pythonpath`` directory next to the component automatically, which is the
supported way to ship a second module.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import traceback

import odslint_core as core
import unohelper
from com.sun.star.awt import XActionListener, XItemListener
from com.sun.star.awt.PosSize import POSSIZE as PosSize_POSSIZE
from com.sun.star.beans import PropertyValue
from com.sun.star.document import XDocumentEventListener
from com.sun.star.frame import XDispatch, XDispatchProvider
from com.sun.star.lang import IllegalArgumentException, XComponent, XInitialization, XServiceInfo
from com.sun.star.ui import XToolPanel, XUIElement, XUIElementFactory
from com.sun.star.ui.UIElementType import TOOLPANEL as UIElementType_TOOLPANEL

IMPLEMENTATION_NAME = "de.fwilhe2.odslint.ProtocolHandler"
PANEL_FACTORY_NAME = "de.fwilhe2.odslint.PanelFactory"
PROTOCOL = "de.fwilhe2.odslint:"
FLAT_FILTER = "OpenDocument Spreadsheet Flat XML"

CALC_SERVICE = "com.sun.star.sheet.SpreadsheetDocument"


def _pv(name, value):
    prop = PropertyValue()
    prop.Name = name
    prop.Value = value
    return prop


class OdslintError(Exception):
    """Something the user needs to be told about in a message box."""


# -- highlight bookkeeping --------------------------------------------------


class Highlighter:
    """Applies and removes the optional cell highlight.

    Calc gives an extension no true overlay, so the only way to tint a cell is
    to set its background — a real document change. That is survivable only if
    it is exactly reversible, so every cell's previous colour is recorded before
    it is touched and restored verbatim afterwards.
    """

    def __init__(self):
        self._saved = {}

    @property
    def is_active(self):
        return bool(self._saved)

    def apply(self, document, findings):
        self.clear(document)
        was_modified = bool(document.isModified())
        sheets = document.Sheets
        for finding in findings:
            if not finding.is_cell_anchored:
                continue
            if not sheets.hasByName(finding.sheet):
                continue
            cell = sheets.getByName(finding.sheet).getCellByPosition(finding.column, finding.row)
            key = (finding.sheet, finding.row, finding.column)
            if key not in self._saved:
                self._saved[key] = cell.CellBackColor
            cell.CellBackColor = core.HIGHLIGHT_COLORS.get(finding.severity, 0xFFF0C0)
        # Tinting a cell dirties the document. If the user had not touched it,
        # put that back — nobody should be prompted to save because they ran a
        # linter.
        if not was_modified:
            document.setModified(False)

    def clear(self, document):
        if not self._saved:
            return
        was_modified = bool(document.isModified())
        sheets = document.Sheets
        for (name, row, column), colour in self._saved.items():
            if not sheets.hasByName(name):
                continue
            sheets.getByName(name).getCellByPosition(column, row).CellBackColor = colour
        self._saved.clear()
        if not was_modified:
            document.setModified(False)


class SaveGuard(unohelper.Base, XDocumentEventListener):
    """Strips highlights before the document is written.

    Without this the tint would be saved into the user's file, and a linting
    artefact that survives in the document is a bug no matter how pretty it is.
    """

    def __init__(self, session):
        self.session = session

    def documentEventOccured(self, event):
        if event.EventName in ("OnSave", "OnSaveAs", "OnSaveTo", "OnPrepareViewClosing"):
            # A listener that raises breaks the save it was called from.
            with contextlib.suppress(Exception):
                self.session.highlighter.clear(self.session.document)

    def disposing(self, event):
        pass


# -- one document's lint state ----------------------------------------------


class Session:
    """What the extension remembers about a single open document."""

    def __init__(self, document):
        self.document = document
        self.findings = []
        self.highlighter = Highlighter()
        self.guard = SaveGuard(self)
        # Older builds do not expose this; losing the save-guard is worth less
        # than refusing to lint at all, so it is optional.
        with contextlib.suppress(Exception):
            document.addDocumentEventListener(self.guard)


# -- the extension ----------------------------------------------------------


class OdslintHandler(unohelper.Base, XServiceInfo, XDispatchProvider, XDispatch, XInitialization):
    def __init__(self, ctx):
        self.ctx = ctx
        self.frame = None
        self._sessions = {}

    # -- XInitialization
    def initialize(self, args):
        if args:
            self.frame = args[0]

    # -- XServiceInfo
    def getImplementationName(self):
        return IMPLEMENTATION_NAME

    def supportsService(self, name):
        return name == "com.sun.star.frame.ProtocolHandler"

    def getSupportedServiceNames(self):
        return ("com.sun.star.frame.ProtocolHandler",)

    # -- XDispatchProvider
    def queryDispatch(self, url, target_frame_name, search_flags):
        if url.Protocol == PROTOCOL:
            return self
        return None

    def queryDispatches(self, requests):
        return tuple(self.queryDispatch(r.FeatureURL, r.FrameName, r.SearchFlags) for r in requests)

    # -- XDispatch
    def addStatusListener(self, listener, url):
        pass

    def removeStatusListener(self, listener, url):
        pass

    def dispatch(self, url, args):
        actions = {
            "lint": self.lint,
            "fix": self.fix_safe,
            "clear": self.clear_highlights,
            "settings": self.edit_settings,
        }
        action = actions.get(url.Path)
        if action is None:
            return
        try:
            action()
        except OdslintError as exc:
            self._message("odslint", str(exc))
        except core.OdslintNotFound as exc:
            self._message("odslint not found", str(exc))
        except core.OdslintFailed as exc:
            self._message("odslint failed", str(exc))
        except Exception:
            self._message("odslint: unexpected error", traceback.format_exc())

    # -- plumbing -----------------------------------------------------------

    def _desktop(self):
        return self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.frame.Desktop", self.ctx
        )

    def _document(self):
        document = None
        if self.frame is not None:
            try:
                document = self.frame.getController().getModel()
            except Exception:
                document = None
        if document is None:
            document = self._desktop().getCurrentComponent()
        if document is None or not document.supportsService(CALC_SERVICE):
            raise OdslintError("Open a Calc spreadsheet first.")
        return document

    def _session(self, document):
        key = document.RuntimeUID
        session = self._sessions.get(key)
        if session is None:
            session = Session(document)
            self._sessions[key] = session
        return session

    def _settings_path(self):
        substitution = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.util.PathSubstitution", self.ctx
        )
        user = unohelper.fileUrlToSystemPath(substitution.substituteVariables("$(user)", True))
        return os.path.join(user, "odslint.json")

    def _settings(self):
        return core.load_settings(self._settings_path())

    def _message(self, title, text):
        toolkit = self.ctx.ServiceManager.createInstanceWithContext(
            "com.sun.star.awt.Toolkit", self.ctx
        )
        parent = self.frame.getContainerWindow() if self.frame is not None else None
        box = toolkit.createMessageBox(
            parent,
            uno_message_type(),
            1,  # BUTTONS_OK
            title,
            text,
        )
        box.execute()
        box.dispose()

    # -- actions ------------------------------------------------------------

    def _lint(self, document, settings):
        """Store the live document to a temp flat file and lint that."""
        handle, temp = tempfile.mkstemp(suffix=".fods", prefix="odslint-")
        os.close(handle)
        try:
            document.storeToURL(
                unohelper.systemPathToFileUrl(temp), (_pv("FilterName", FLAT_FILTER),)
            )
            interpreter = core.find_interpreter(settings.get("interpreter"))
            return core.run_odslint(interpreter, temp, unsafe=settings.get("unsafe_fixes", False))
        finally:
            with contextlib.suppress(OSError):
                os.unlink(temp)

    def lint(self):
        document = self._document()
        settings = self._settings()
        session = self._session(document)
        session.findings = self._lint(document, settings)

        if settings.get("highlight", True):
            session.highlighter.apply(document, session.findings)

        FindingsDialog(self, document, session).show()

    def clear_highlights(self):
        document = self._document()
        self._session(document).highlighter.clear(document)

    def fix_safe(self):
        document = self._document()
        settings = self._settings()
        session = self._session(document)
        if not session.findings:
            session.findings = self._lint(document, settings)

        applied = self.apply_edits(
            document, core.collect_edits(session.findings, unsafe=settings.get("unsafe_fixes"))
        )
        if not applied:
            self._message("odslint", "Nothing to fix.")
            return
        # The document changed underneath the report, so re-run it.
        session.findings = self._lint(document, settings)
        if settings.get("highlight", True):
            session.highlighter.apply(document, session.findings)
        self._message(
            "odslint",
            f"Applied {applied} fix{'' if applied == 1 else 'es'}."
            "\n\nUndo reverts them all at once.",
        )

    def apply_edits(self, document, edits):
        """Replay edits through UNO, as one undoable step.

        Formulas go in as ``formula_a1``: ``XCell.setFormula`` expects Calc's own
        A1 convention, and handing it the stored ODF form does not fail — it
        silently stores a formula that evaluates to zero.
        """
        if not edits:
            return 0

        sheets = document.Sheets
        undo = None
        try:
            undo = document.getUndoManager()
            undo.enterUndoContext("odslint fix")
        except Exception:
            undo = None

        applied = 0
        try:
            for edit in edits:
                if not sheets.hasByName(edit["sheet"]):
                    continue
                cell = sheets.getByName(edit["sheet"]).getCellByPosition(
                    edit["column"], edit["row"]
                )
                if edit["kind"] == "formula":
                    formula = edit.get("formula_a1")
                    if not formula:
                        # No A1 spelling means odslint could not express this
                        # one safely; skipping beats storing something broken.
                        continue
                    cell.setFormula(formula)
                elif edit["kind"] == "number":
                    cell.setValue(float(edit["value"]))
                else:
                    continue
                applied += 1
        finally:
            if undo is not None:
                with contextlib.suppress(Exception):
                    undo.leaveUndoContext()
        return applied

    def go_to(self, document, finding):
        if not finding.is_cell_anchored:
            return
        sheets = document.Sheets
        if not sheets.hasByName(finding.sheet):
            return
        sheet = sheets.getByName(finding.sheet)
        controller = document.getCurrentController()
        controller.setActiveSheet(sheet)
        controller.select(sheet.getCellByPosition(finding.column, finding.row))

    def edit_settings(self):
        SettingsDialog(self).show()


def uno_message_type():
    from com.sun.star.awt.MessageBoxType import MESSAGEBOX

    return MESSAGEBOX


# -- dialogs ----------------------------------------------------------------


class _DialogBase:
    """Builds an AWT dialog in code rather than shipping a .xdl.

    One fewer file to keep in step with the schema, and the layout is simple
    enough that the code reads better than the XML would.
    """

    def __init__(self, handler):
        self.handler = handler
        self.ctx = handler.ctx
        self.dialog = None

    def _create(self, name):
        return self.ctx.ServiceManager.createInstanceWithContext(name, self.ctx)

    def _build(self, title, width, height):
        dialog = self._create("com.sun.star.awt.UnoControlDialog")
        model = self._create("com.sun.star.awt.UnoControlDialogModel")
        model.Title = title
        model.Width = width
        model.Height = height
        dialog.setModel(model)
        self.dialog = dialog
        self.model = model
        return dialog

    def _add(self, kind, name, **props):
        control = self.model.createInstance("com.sun.star.awt." + kind)
        for key, value in props.items():
            setattr(control, key, value)
        self.model.insertByName(name, control)
        return control

    def _run(self):
        toolkit = self._create("com.sun.star.awt.Toolkit")
        parent = self.handler.frame.getContainerWindow() if self.handler.frame is not None else None
        self.dialog.createPeer(toolkit, parent)
        self.dialog.execute()
        self.dialog.dispose()


class FindingsDialog(_DialogBase, unohelper.Base, XActionListener, XItemListener):
    """The findings list. Selecting a row jumps to its cell."""

    def __init__(self, handler, document, session):
        _DialogBase.__init__(self, handler)
        self.document = document
        self.session = session

    def show(self):
        findings = self.session.findings
        self._build("odslint", 320, 210)

        summary = "No problems found." if not findings else _summarize(findings)
        self._add(
            "UnoControlFixedTextModel",
            "summary",
            PositionX=8,
            PositionY=6,
            Width=304,
            Height=10,
            Label=summary,
        )
        self._add(
            "UnoControlListBoxModel",
            "list",
            PositionX=8,
            PositionY=20,
            Width=304,
            Height=140,
            StringItemList=tuple(f.label() for f in findings),
        )
        self._add(
            "UnoControlFixedTextModel",
            "hint",
            PositionX=8,
            PositionY=164,
            Width=304,
            Height=18,
            Label="",
            MultiLine=True,
        )
        self._add(
            "UnoControlButtonModel",
            "fix",
            PositionX=8,
            PositionY=188,
            Width=70,
            Height=14,
            Label="Fix",
            Enabled=any(f.is_fixable for f in findings),
        )
        self._add(
            "UnoControlButtonModel",
            "clear",
            PositionX=84,
            PositionY=188,
            Width=90,
            Height=14,
            Label="Clear highlights",
        )
        self._add(
            "UnoControlButtonModel",
            "close",
            PositionX=250,
            PositionY=188,
            Width=62,
            Height=14,
            Label="Close",
        )

        dialog = self.dialog
        dialog.getControl("list").addItemListener(self)
        for name in ("fix", "clear", "close"):
            control = dialog.getControl(name)
            control.setActionCommand(name)
            control.addActionListener(self)

        self._run()

    # -- XItemListener
    def itemStateChanged(self, event):
        index = self.dialog.getControl("list").getSelectedItemPos()
        if 0 <= index < len(self.session.findings):
            finding = self.session.findings[index]
            self.dialog.getControl("hint").setText(finding.hint or "")
            self.handler.go_to(self.document, finding)

    # -- XActionListener
    def actionPerformed(self, event):
        command = event.ActionCommand
        if command == "close":
            self.dialog.endExecute()
        elif command == "clear":
            self.session.highlighter.clear(self.document)
        elif command == "fix":
            self.dialog.endExecute()
            self.handler.fix_safe()

    def disposing(self, event):
        pass


class SettingsDialog(_DialogBase, unohelper.Base, XActionListener):
    def show(self):
        settings = self.handler._settings()
        self._build("odslint settings", 300, 120)

        self._add(
            "UnoControlFixedTextModel",
            "label",
            PositionX=8,
            PositionY=8,
            Width=284,
            Height=18,
            MultiLine=True,
            Label="Path to the odslint executable (leave empty to search PATH):",
        )
        self._add(
            "UnoControlEditModel",
            "interpreter",
            PositionX=8,
            PositionY=30,
            Width=284,
            Height=14,
            Text=settings.get("interpreter", ""),
        )
        self._add(
            "UnoControlCheckBoxModel",
            "highlight",
            PositionX=8,
            PositionY=52,
            Width=284,
            Height=12,
            Label="Highlight flagged cells",
            State=1 if settings.get("highlight", True) else 0,
        )
        self._add(
            "UnoControlCheckBoxModel",
            "unsafe",
            PositionX=8,
            PositionY=68,
            Width=284,
            Height=12,
            Label="Offer unsafe fixes too",
            State=1 if settings.get("unsafe_fixes", False) else 0,
        )
        self._add(
            "UnoControlButtonModel",
            "save",
            PositionX=160,
            PositionY=96,
            Width=60,
            Height=14,
            Label="Save",
        )
        self._add(
            "UnoControlButtonModel",
            "cancel",
            PositionX=230,
            PositionY=96,
            Width=60,
            Height=14,
            Label="Cancel",
        )

        for name in ("save", "cancel"):
            control = self.dialog.getControl(name)
            control.setActionCommand(name)
            control.addActionListener(self)

        self._run()

    def actionPerformed(self, event):
        if event.ActionCommand == "save":
            core.save_settings(
                self.handler._settings_path(),
                {
                    "interpreter": self.dialog.getControl("interpreter").getText().strip(),
                    "highlight": bool(self.dialog.getControl("highlight").getState()),
                    "unsafe_fixes": bool(self.dialog.getControl("unsafe").getState()),
                },
            )
        self.dialog.endExecute()

    def disposing(self, event):
        pass


def _summarize(findings):
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    fixable = sum(1 for f in findings if f.is_fixable)
    parts = []
    if errors:
        parts.append(f"{errors} error{'' if errors == 1 else 's'}")
    if warnings:
        parts.append(f"{warnings} warning{'' if warnings == 1 else 's'}")
    text = f"{len(findings)} problem{'' if len(findings) == 1 else 's'}"
    if parts:
        text += " (" + ", ".join(parts) + ")"
    if fixable:
        text += f" — {fixable} fixable"
    return text


g_ImplementationHelper = unohelper.ImplementationHelper()
g_ImplementationHelper.addImplementation(
    OdslintHandler, IMPLEMENTATION_NAME, ("com.sun.star.frame.ProtocolHandler",)
)


# -- sidebar panel ----------------------------------------------------------
#
# The docked panel is the point of the exercise: a modal dialog cannot be the
# "linter in an IDE" experience, because you cannot edit the cell it is telling
# you about while it is open. The panel stays put while you work.
#
# The wiring is: Sidebar.xcu declares a deck and a panel whose ImplementationURL
# is private:resource/toolpanel/<factory>/<panel>, Factories.xcu points that
# factory name at the implementation below, and the factory hands back an
# XUIElement whose getRealInterface() is an XToolPanel wrapping a plain AWT
# container window.


class OdslintToolPanel(unohelper.Base, XToolPanel):
    """The window inside the deck.

    ``Window`` has to be a real instance attribute: pyuno maps an IDL read-only
    attribute onto a Python attribute, not onto a ``getWindow`` method, and the
    sidebar reads it as a property. A getter alone fails with "Property Window
    is unknown" at the moment the deck opens.
    """

    def __init__(self, window):
        self.Window = window

    def getWindow(self):
        return self.Window

    def createAccessible(self, parent):
        return self.Window.getAccessibleContext() if self.Window else None


class OdslintPanelElement(unohelper.Base, XUIElement, XComponent):
    def __init__(self, ctx, frame, parent_window, resource_url):
        self.ctx = ctx
        self.Frame = frame
        self.ResourceURL = resource_url
        self.Type = UIElementType_TOOLPANEL
        self._listeners = []
        self.view = PanelView(ctx, frame, parent_window)
        self.panel = OdslintToolPanel(self.view.window)

    # -- XUIElement
    def getRealInterface(self):
        return self.panel

    # -- XComponent
    def dispose(self):
        for listener in list(self._listeners):
            with contextlib.suppress(Exception):
                listener.disposing(None)
        self._listeners = []
        with contextlib.suppress(Exception):
            self.view.window.dispose()

    def addEventListener(self, listener):
        self._listeners.append(listener)

    def removeEventListener(self, listener):
        with contextlib.suppress(ValueError):
            self._listeners.remove(listener)


class PanelView(unohelper.Base, XActionListener, XItemListener):
    """Controls of the Problems panel, laid out in code.

    Sized in pixels rather than dialog map units: the panel is handed a real
    parent window, not a dialog, so there is no map-unit conversion to lean on.
    """

    def __init__(self, ctx, frame, parent_window):
        self.ctx = ctx
        self.frame = frame
        self.handler = OdslintHandler(ctx)
        self.handler.frame = frame
        self.findings = []
        self.window = self._build(parent_window)

    def _create(self, name):
        return self.ctx.ServiceManager.createInstanceWithContext(name, self.ctx)

    def _build(self, parent_window):
        container = self._create("com.sun.star.awt.UnoControlContainer")
        model = self._create("com.sun.star.awt.UnoControlContainerModel")
        container.setModel(model)
        container.createPeer(self._create("com.sun.star.awt.Toolkit"), parent_window)

        self.summary = self._child(
            container, "FixedText", "summary", 4, 4, 300, 18, Label="Not linted yet."
        )
        self.listbox = self._child(container, "ListBox", "list", 4, 26, 300, 220)
        self.hint = self._child(container, "FixedText", "hint", 4, 250, 300, 34, MultiLine=True)
        self.lint_button = self._child(container, "Button", "lint", 4, 290, 74, 24, Label="Lint")
        self.fix_button = self._child(
            container, "Button", "fix", 82, 290, 74, 24, Label="Fix", Enabled=False
        )
        self.clear_button = self._child(
            container, "Button", "clear", 160, 290, 90, 24, Label="Unhighlight"
        )

        self.listbox.addItemListener(self)
        for name, control in (
            ("lint", self.lint_button),
            ("fix", self.fix_button),
            ("clear", self.clear_button),
        ):
            control.setActionCommand(name)
            control.addActionListener(self)

        container.setVisible(True)
        return container

    def _child(self, container, kind, name, x, y, width, height, **props):
        control = self._create("com.sun.star.awt.UnoControl" + kind)
        model = self._create("com.sun.star.awt.UnoControl" + kind + "Model")
        for key, value in props.items():
            setattr(model, key, value)
        control.setModel(model)
        container.addControl(name, control)
        control.setPosSize(x, y, width, height, PosSize_POSSIZE)
        return control

    # -- actions
    def _document(self):
        return self.frame.getController().getModel()

    def refresh(self):
        document = self._document()
        settings = self.handler._settings()
        session = self.handler._session(document)
        self.findings = self.handler._lint(document, settings)
        session.findings = self.findings

        self.listbox.getModel().StringItemList = tuple(f.label() for f in self.findings)
        self.summary.getModel().Label = (
            "No problems found." if not self.findings else _summarize(self.findings)
        )
        self.fix_button.getModel().Enabled = any(f.is_fixable for f in self.findings)
        self.hint.getModel().Label = ""
        if settings.get("highlight", True):
            session.highlighter.apply(document, self.findings)

    def itemStateChanged(self, event):
        index = self.listbox.getSelectedItemPos()
        if 0 <= index < len(self.findings):
            finding = self.findings[index]
            self.hint.getModel().Label = finding.hint or ""
            self.handler.go_to(self._document(), finding)

    def actionPerformed(self, event):
        try:
            if event.ActionCommand == "lint":
                self.refresh()
            elif event.ActionCommand == "fix":
                self.handler.fix_safe()
                self.refresh()
            elif event.ActionCommand == "clear":
                document = self._document()
                self.handler._session(document).highlighter.clear(document)
        except (OdslintError, core.OdslintNotFound, core.OdslintFailed) as exc:
            self.summary.getModel().Label = str(exc).splitlines()[0][:120]
            self.handler._message("odslint", str(exc))
        except Exception:
            self.handler._message("odslint: unexpected error", traceback.format_exc())

    def disposing(self, event):
        pass


class OdslintPanelFactory(unohelper.Base, XUIElementFactory, XServiceInfo):
    """Builds the panel when the sidebar deck is opened."""

    def __init__(self, ctx):
        self.ctx = ctx

    def createUIElement(self, resource_url, arguments):
        frame = None
        parent_window = None
        for argument in arguments:
            if argument.Name == "Frame":
                frame = argument.Value
            elif argument.Name == "ParentWindow":
                parent_window = argument.Value
        if frame is None or parent_window is None:
            raise IllegalArgumentException("no Frame/ParentWindow for the odslint panel", None, 0)
        return OdslintPanelElement(self.ctx, frame, parent_window, resource_url)

    def getImplementationName(self):
        return PANEL_FACTORY_NAME

    def supportsService(self, name):
        return name == "com.sun.star.ui.UIElementFactory"

    def getSupportedServiceNames(self):
        return ("com.sun.star.ui.UIElementFactory",)


g_ImplementationHelper.addImplementation(
    OdslintPanelFactory, PANEL_FACTORY_NAME, ("com.sun.star.ui.UIElementFactory",)
)
