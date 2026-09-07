from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QComboBox,
    QLineEdit, QPlainTextEdit, QPushButton, QLabel, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QMessageBox,
)

from ..camds.import_control import brief
from ..camds.import_plan import ImportRequest
from ..camds.material_classifications import classification_code, describe, known_codes, sort_key
from ..camds.operations import SearchRequest, KINDS
from ..workers.operations_worker import OperationsWorker
from .import_dialog import ImportDialog

# What the operator is told the moment an action is submitted. Keyed by the
# same names as ACTION_POLICY, and tested to stay in step with it: an action
# added to one map and not the others used to reach the user as a KeyError.
SUBMIT_STATUS = {
    "search": "Searching CAMDS…",
    "discover_classifications": "Opening the classification wizard to record it; nothing is created…",
    "check_substances": "Looking up every substance of the parsed tree; nothing is created…",
    "import_tree": "Transferring parsed tree; saving each step…",
    "login": "Signing in on this browser session…",
    "open_browser": "Opening a CAMDS window on the current session…",
    "close_browser": "Closing the window; the session is kept…",
}


class CamdsTab(QWidget):
    log_message = Signal(str)
    operation_status = Signal(str, bool)
    node_progress = Signal(object)
    session_changed = Signal(str)
    login_required = Signal()
    session_ready = Signal()
    # An import runs for hours; whoever started it is not watching it end.
    import_started = Signal()
    import_finished = Signal(object)
    import_failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.worker = None
        self.busy = False
        self.editor_open = False
        self.importing = False
        self.last_error = ""
        self.parsed_root = None
        # Named in the result note; kept so a failure can point at it.
        self.journal_path = None
        self.session = "UNKNOWN"
        self.browser_open = False
        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.open_button = QPushButton("Open CAMDS browser")
        self.open_button.setToolTip(
            "Open a window on the current CAMDS session. The session lives in the app, "
            "not in the window.")
        self.close_button = QPushButton("Close browser window")
        self.close_button.setToolTip(
            "Close the window only. The CAMDS session is kept, and Search, Create and "
            "the tree import go on working without it.")
        bar.addWidget(self.open_button)
        bar.addWidget(self.close_button)
        self.sign_in_button = QPushButton("Sign in to CAMDS")
        bar.addWidget(self.sign_in_button)
        self.import_tree_button = QPushButton("Import parsed tree…")
        bar.addWidget(self.import_tree_button)
        # Two buttons used to sit here. Checking the substance catalogue is part
        # of Validate now, because it answers a validation question and nobody
        # should have to know to press it separately; testing the API session is
        # the first thing that check does anyway, and every import proves the
        # session before it allocates an id. Recording the classification wizard
        # is a maintenance task, not part of an import, so it lives in the CAMDS
        # menu. All three still dispatch through the same worker actions.
        root.addLayout(bar)
        controls = QHBoxLayout()
        self.pause_button = QPushButton("Pause import")
        self.resume_button = QPushButton("Resume import")
        self.stop_button = QPushButton("Stop import")
        self.import_status = QLabel("Import idle")
        self.import_status.setWordWrap(True)
        for widget in (self.pause_button, self.resume_button, self.stop_button):
            widget.setEnabled(False)
            controls.addWidget(widget)
        controls.addWidget(self.import_status, 1)
        root.addLayout(controls)
        self.status = QLabel("Open a browser session. Sign in there if requested; select English on CAMDS.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.forms = QWidget()
        forms_layout = QHBoxLayout(self.forms)
        search_group = QGroupBox("Search CAMDS")
        search_form = QFormLayout(search_group)
        self.search_kind = QComboBox()
        self.search_kind.addItems(KINDS)
        self.search_name, self.search_id, self.search_number, self.search_cas = (QLineEdit() for _ in range(4))
        self.search_source = QComboBox()
        self.search_source.addItems(("Own", "Published", "Accepted", "All"))
        for label, widget in (("Type", self.search_kind), ("Name", self.search_name), ("CAMDS ID", self.search_id), ("Part / Material No.", self.search_number), ("CAS", self.search_cas), ("Source", self.search_source)):
            search_form.addRow(label, widget)
        self.search_button = QPushButton("Search")
        search_form.addRow(self.search_button)
        forms_layout.addWidget(search_group)
        root.addWidget(self.forms)
        self.results = QTableWidget()
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        root.addWidget(self.results)
        self.open_button.clicked.connect(self.open_session)
        self.close_button.clicked.connect(self.close_browser)
        self.search_button.clicked.connect(self.search)
        self.import_tree_button.clicked.connect(self.review_import)
        self.sign_in_button.clicked.connect(self.request_sign_in)
        self.pause_button.clicked.connect(self.pause_import)
        self.resume_button.clicked.connect(self.resume_import)
        self.stop_button.clicked.connect(self.stop_import)
        self.search_kind.currentTextChanged.connect(self._kind_changed)
        self._kind_changed()
        self._update()

    def _kind_changed(self, *_args) -> None:
        substance = self.search_kind.currentText() == "Basic Substance"
        self.search_cas.setEnabled(substance)
        self.search_id.setEnabled(not substance)
        self.search_number.setEnabled(not substance)
        self.search_source.setEnabled(not substance)

    def set_document(self, document) -> None:
        self.parsed_root = document.root.to_dict()
        self._update()

    def review_import(self) -> None:
        # Name the precondition that actually failed; a generic message sent
        # operators back to steps they had already completed.
        if self.parsed_root is None:
            self.status.setText("Parse an IMDS PDF before importing the tree.")
            return
        if self.worker is None:
            self.status.setText("Open a CAMDS browser session before importing the tree.")
            return
        if self.busy:
            self.status.setText("A CAMDS operation is still running. Wait for it to finish, then import the tree.")
            return
        if self.editor_open:
            self.status.setText(
                "An MDS editor is open in the browser, so the tree import cannot start. "
                "Use Save open draft to keep it, then Leave editor to return to Search — or Close browser session.")
            return
        dialog = ImportDialog(self.parsed_root, self)
        if dialog.exec() and dialog.request:
            self.import_started.emit()
            self._submit("import_tree", dialog.request, resume=dialog.resume.isChecked(),
                         reuse=dialog.reuse.isChecked(),
                         release=dialog.release.isChecked())

    def can_check_substances(self) -> str:
        """Why the catalogue check cannot run now, or "" if it can."""
        if self.parsed_root is None:
            return "no parsed IMDS document"
        if self.worker is None or self.session != "AUTHENTICATED":
            return "no signed-in CAMDS session"
        if self.busy or self.editor_open:
            return "CAMDS is busy"
        return ""

    def check_substances(self) -> bool:
        """Look up every distinct substance of the parsed tree. Read-only."""
        reason = self.can_check_substances()
        if reason:
            self.status.setText(f"Substance check skipped: {reason}.")
            return False
        self._submit("check_substances", ImportRequest(self.parsed_root))
        return True

    def open_session(self) -> None:
        """Start the session, or put another window on the one already running."""
        if self.worker is not None:
            self._submit("open_browser", None)
            return
        self.worker = OperationsWorker(Path(".runtime/camds_storage_state.json"), self)
        self.last_error = ""
        self.worker.ready.connect(self._ready)
        self.worker.operation_progress.connect(self._progress)
        self.worker.node_progress.connect(self._node_progress)
        self.worker.session_changed.connect(self._session_changed)
        self.worker.browser_changed.connect(self._browser_changed)
        self.worker.login_stage.connect(self.log_message.emit)
        self.worker.notice.connect(self._notice)
        self.worker.result.connect(self._result)
        self.worker.failed.connect(self._failed)
        self.worker.session_error.connect(lambda message: self._failed(message, self.editor_open))
        self.worker.finished.connect(self._finished)
        self.busy = True
        self.status.setText("Opening CAMDS browser…")
        self._update()
        self.worker.start()

    def close_browser(self) -> None:
        """Close the window and keep the session. Closing it by hand does the same."""
        if self.worker is not None:
            self._submit("close_browser", None)

    def stop_session(self) -> None:
        if self.worker:
            self.worker.stop()
            self.busy = True
            self.status.setText("Closing browser. Any unsaved form will not be preserved.")
            self._update()

    def _browser_changed(self, open_now: bool) -> None:
        self.browser_open = open_now
        self._update()

    def _ready(self) -> None:
        if self.worker is None or self.worker.stopping.is_set():
            return
        self.busy = False
        self.status.setText("Browser open. Complete login/slider there if needed and use English, then run Search or Create.")
        self._update()
        self.session_ready.emit()

    def _notice(self, message: str) -> None:
        """Non-fatal condition: the session stays usable."""
        self.status.setText(message)
        self.log_message.emit(message)

    def _progress(self, message: str) -> None:
        self.busy = True
        self.status.setText(message + " Keep the CAMDS browser open…")
        self.log_message.emit(message)
        self.operation_status.emit(message, False)
        self._update()

    def _submit(self, action, request, **options) -> None:
        if not self.worker or self.busy or (self.editor_open and action not in ("save", "leave_editor")):
            return
        try:
            if request is not None and hasattr(request, "validate"):
                request.validate()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.busy = True
        self.importing = action == "import_tree"
        self.last_error = ""
        self.results.setRowCount(0)
        self.status.setText(SUBMIT_STATUS[action])
        if options:
            self.worker.submit(action, request, **options)
        else:
            self.worker.submit(action, request)
        self._update()

    def request_sign_in(self) -> None:
        """Ask the window for credentials; login runs on this same browser session."""
        self.login_required.emit()

    def sign_in(self, credentials) -> None:
        self._submit("login", credentials)

    def pause_import(self) -> None:
        if self.worker and self.importing:
            self.worker.control.pause()
            self.import_status.setText("Pause requested; the import stops after the current CAMDS step completes.")
            self._update()

    def resume_import(self) -> None:
        if self.worker and self.importing:
            self.worker.control.resume()
            self.import_status.setText("Resuming…")
            self._update()

    def stop_import(self) -> None:
        if self.worker and self.importing:
            self.worker.control.stop()
            self.import_status.setText("Stop requested; already saved objects are kept and not rolled back.")
            self._update()

    def _node_progress(self, event) -> None:
        self.busy = True
        detail = f"{event.counter} · {event.kind or '-'} · {event.parent_path}"
        if event.field_label:
            detail += f" · field: {event.field_label}"
        self.import_status.setText(f"{event.message} — {detail}")
        self.node_progress.emit(event)
        if event.event in ("node_failed", "stopped", "material_readback_verified", "complete_readback_verified"):
            self.log_message.emit(event.message)
        self._update()

    def _session_changed(self, status: str) -> None:
        self.session = status
        self.session_changed.emit(status)
        if status == "EXPIRED":
            self.status.setText("CAMDS session expired in this browser. Sign in again before running operations.")
            self.log_message.emit("CAMDS session expired; operations are blocked until sign-in")
        self._update()

    def search(self) -> None:
        substance = self.search_kind.currentText() == "Basic Substance"
        self._submit("search", SearchRequest(self.search_kind.currentText(), self.search_name.text().strip(), "" if substance else self.search_id.text().strip(), "" if substance else self.search_number.text().strip(), self.search_cas.text().strip() if substance else "", self.search_source.currentText()))

    def discover_classifications(self) -> None:
        """Record the material classification wizard so more than 1.1.1 can be supported."""
        confirm = QMessageBox.question(
            self, "Record classification wizard",
            "Open the CAMDS 'Creation of a new material' dialog and record its structure?\n\n"
            "CAMDS allocates an MDS ID only when Next opens the editor. This stops at the dialog and never "
            "presses Next, so no MDS is created and no ID is consumed. The dialog is closed again and a "
            "snapshot is written to debug/material-classifications/.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            self._submit("discover_classifications", None)

        confirm = QMessageBox.question(
            self, "Leave MDS editor",
            "Leave the open MDS editor and return to Search?\n\n"
            "Anything you have not saved is discarded. A draft you already saved keeps its allocated CAMDS ID "
            "and is not deleted. Nothing is sent or submitted.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm == QMessageBox.StandardButton.Yes:
            self._submit("leave_editor", None)

    def _result(self, result) -> None:
        self.busy = False
        self.importing = False
        self.editor_open = result.get("editor_open", result["kind"] == "create")
        if result["kind"] == "login":
            self.session = "AUTHENTICATED"
            self.session_changed.emit(self.session)
        if result["kind"] == "import_tree":
            self.import_status.setText(f"Import complete: {result.get('nodes', 0)} / {result.get('total', 0)} steps verified.")
            note = str(result.get("note") or "")
            if "Journal: " in note:
                self.journal_path = note.split("Journal: ", 1)[1].strip()
            self.import_finished.emit(result)
        # An operation that CAMDS accepted can still hold findings the operator
        # has to see; a completed run must not bury them.
        for finding in (result.get("warnings") or []) + (result.get("skipped") or []):
            self.log_message.emit("Reported: " + finding)
        if result["kind"] == "search":
            self.results.setColumnCount(len(result["columns"]))
            self.results.setHorizontalHeaderLabels(result["columns"])
            self.results.setRowCount(len(result["rows"]))
            for r, row in enumerate(result["rows"]):
                for c, value in enumerate(row):
                    self.results.setItem(r, c, QTableWidgetItem(value))
            self.results.resizeColumnsToContents()
        self.status.setText(result.get("identity", "") + " " + result["note"])
        self.log_message.emit(result["note"])
        self.operation_status.emit(result["note"], True)
        self._update()

    def _failed(self, message, editor_open) -> None:
        self.busy = False
        was_importing, self.importing = self.importing, False
        short = brief(message)
        self.import_status.setText("Import stopped: " + brief(message, lines=1))
        self.editor_open = editor_open
        suffix = " The editor may contain a partially filled root. Review it and the import journal; completed Save operations are not rolled back. No automatic retry." if editor_open else ""
        self.last_error = short + suffix
        self.status.setText(short + suffix)
        # The log keeps the full text, including any page snapshot.
        self.log_message.emit(message + suffix)
        self._update()
        if editor_open:
            self.forms.setEnabled(False)
        if was_importing:
            self.import_failed.emit(message)

    def _finished(self) -> None:
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self.busy = False
        self.importing = False
        self.session = "UNKNOWN"
        self.browser_open = False
        self.session_changed.emit(self.session)
        self.editor_open = False
        self.status.setText((self.last_error + " " if self.last_error else "") + "Browser session closed. Open a new session to continue.")
        self._update()

    def _update(self) -> None:
        stopping = self.worker is not None and self.worker.stopping.is_set()
        idle = self.worker is not None and not stopping and not self.busy
        # A window is a view on the session, not the session itself, so opening
        # and closing one stays available the whole time a session is running.
        self.open_button.setEnabled(self.worker is None or (idle and not self.browser_open))
        self.close_button.setEnabled(idle and self.browser_open)
        self.forms.setEnabled(idle and not self.editor_open)
        self.import_tree_button.setEnabled(idle and not self.editor_open and self.parsed_root is not None)
        self.sign_in_button.setEnabled(idle and not self.editor_open)
        control = getattr(self.worker, "control", None)
        running = self.importing and control is not None and not stopping
        self.pause_button.setEnabled(running and not control.paused and not control.stopping)
        self.resume_button.setEnabled(running and control.paused)
        self.stop_button.setEnabled(running and not control.stopping)
