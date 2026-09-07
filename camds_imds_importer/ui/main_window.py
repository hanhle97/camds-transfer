from __future__ import annotations

import json
import time
from pathlib import Path

import pymupdf
from PySide6.QtCore import QThread, Qt, QObject, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from ..camds.browser import BrowserConfig, CamdsBrowser
from ..camds.login import LoginStatus
from ..core.credentials import CredentialManager, Credentials
from ..camds.import_control import brief
from ..core.progress import ImportProgress, ImportStage
from ..core.state_machine import AppState, ApplicationStateMachine
from ..parser.models import MDSDocument
from ..workers.camds_worker import CamdsLoginWorker
from ..workers.discovery_worker import DiscoveryWorker
from ..workers.parser_worker import ParserWorker
from ..workers.validation_worker import ValidationWorker
from .import_report import failure, summary
from .logs_tab import LogsTab
from .status_light import Lamp, StatusLight
from .overview_tab import OverviewTab
from .progress_tab import ProgressTab
from .settings_dialog import SettingsDialog
from .tree_tab import TreeTab
from .validation_tab import ValidationTab
from .camds_tab import CamdsTab
from ..camds.dry_run import write_dry_run_plan
from ..core.exporter import export_excel, export_pdf


class WorkerThread(QThread):
    """Run a QObject worker explicitly; avoids relying on QThread.started delivery."""

    def __init__(self, worker: QObject, parent=None) -> None:
        super().__init__(parent)
        self.worker = worker
        worker.moveToThread(self)

    def run(self) -> None:
        self.worker.run()


# States where nothing is running. At rest the stage says what the state says:
# the two labels describe the same thing, and a worker's last stage text -
# "Validating" - must not be left standing next to "Ready".
RESTING_STATES = {
    AppState.NO_DOCUMENT,
    AppState.DOCUMENT_LOADED,
    AppState.PARSED,
    AppState.READY,
    AppState.COMPLETED,
    AppState.FAILED,
    AppState.CAMDS_AUTHENTICATED,
    AppState.CAMDS_LOGIN_REQUIRED,
}

# What each resting state means at a glance. Anything not named here is work in
# progress, which is what BUSY says.
LAMP_FOR_STATE = {
    AppState.NO_DOCUMENT: Lamp.IDLE,
    AppState.DOCUMENT_LOADED: Lamp.IDLE,
    AppState.PARSED: Lamp.OK,
    AppState.READY: Lamp.OK,
    AppState.COMPLETED: Lamp.OK,
    AppState.CAMDS_AUTHENTICATED: Lamp.OK,
    AppState.CAMDS_LOGIN_REQUIRED: Lamp.WARN,
    AppState.PAUSED: Lamp.WARN,
    AppState.FAILED: Lamp.ERROR,
}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CAMDS IMDS Importer")
        self.resize(1250, 820)
        self.state_machine = ApplicationStateMachine()
        self.credentials = CredentialManager()
        self.document: MDSDocument | None = None
        self.statistics = None
        self.source_path: Path | None = None
        self.authenticated = False
        self._threads: list[QThread] = []
        self.progress = ImportProgress()
        self._active_login_worker: CamdsLoginWorker | None = None
        self._parse_started_at: float | None = None
        self._parse_seconds: float = 0.0
        self._import_started_at: float | None = None
        self._selected_page_count: int = 0
        self._pending_login: Credentials | None = None
        self._build_ui()
        self._build_menu()
        self.state_machine.state_changed.connect(self._apply_state)
        self._apply_state(self.state_machine.state.value)
        # First line of every log: a stale executable answered three runs with a
        # defect already fixed in the source, and nothing on screen said so.
        from ..build_info import build_stamp
        self.logs_tab.append("INFO", "Build: " + build_stamp())

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        summary = QGroupBox("Workflow")
        grid = QGridLayout(summary)
        self.import_button = QPushButton("1. Import IMDS PDF")
        self.file_label = QLabel("No PDF selected")
        self.metadata_label = QLabel("IMDS ID: -    Part No: -    Weight: -")
        self.status_label = StatusLight("Status: Ready", Lamp.IDLE)
        self.connection_label = StatusLight("CAMDS: Not connected", Lamp.IDLE)
        self.overall = QProgressBar()
        self.stage_label = QLabel("Current stage: Idle")
        self.node_label = QLabel("Current node: -")
        grid.addWidget(self.import_button, 0, 0)
        grid.addWidget(self.file_label, 0, 1, 1, 3)
        grid.addWidget(self.metadata_label, 1, 0, 1, 4)
        grid.addWidget(self.status_label, 2, 0, 1, 2)
        grid.addWidget(self.connection_label, 2, 2, 1, 2)
        grid.addWidget(QLabel("Overall progress"), 3, 0)
        grid.addWidget(self.overall, 3, 1, 1, 3)
        grid.addWidget(self.stage_label, 4, 0, 1, 2)
        grid.addWidget(self.node_label, 4, 2, 1, 2)
        buttons = QHBoxLayout()
        self.parse_button = QPushButton("Parse")
        self.validate_button = QPushButton("Validate")
        self.start_button = QPushButton("Start Import")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop")
        self.mode = QComboBox()
        self.mode.addItems(("PARSE_ONLY", "DRY_RUN", "IMPORT_TREE"))
        self.mode.setCurrentText("DRY_RUN")
        buttons.addWidget(QLabel("Mode:"))
        buttons.addWidget(self.mode)
        for button in (self.parse_button, self.validate_button, self.start_button, self.pause_button, self.resume_button, self.stop_button):
            buttons.addWidget(button)
        grid.addLayout(buttons, 5, 0, 1, 4)
        root.addWidget(summary)
        self.tabs = QTabWidget()
        self.overview_tab = OverviewTab()
        self.tree_tab = TreeTab()
        self.validation_tab = ValidationTab()
        self.progress_tab = ProgressTab()
        self.logs_tab = LogsTab()
        self.camds_tab = CamdsTab()
        self.camds_tab.log_message.connect(lambda message: self.logs_tab.append("CAMDS", message))
        self.camds_tab.operation_status.connect(self._camds_operation_status)
        self.camds_tab.node_progress.connect(self._camds_node_progress)
        self.camds_tab.session_changed.connect(self._camds_session_changed)
        self.camds_tab.login_required.connect(self.login_from_menu)
        self.camds_tab.session_ready.connect(self._camds_session_ready)
        self.camds_tab.import_started.connect(
            lambda: setattr(self, "_import_started_at", time.monotonic()))
        self.camds_tab.import_finished.connect(self._import_finished)
        self.camds_tab.import_failed.connect(self._import_failed)
        for title, widget in (("Overview", self.overview_tab), ("MDS Tree", self.tree_tab), ("Validation", self.validation_tab), ("CAMDS Search / Create", self.camds_tab), ("Progress", self.progress_tab), ("Logs", self.logs_tab)):
            self.tabs.addTab(widget, title)
        root.addWidget(self.tabs)
        self.setCentralWidget(central)
        self.import_button.clicked.connect(self.select_pdf)
        self.parse_button.clicked.connect(self.start_parse)
        self.validate_button.clicked.connect(self.start_validation)
        self.start_button.clicked.connect(self._import_not_available)
        self.pause_button.clicked.connect(self.camds_tab.pause_import)
        self.resume_button.clicked.connect(self.camds_tab.resume_import)
        self.stop_button.clicked.connect(self.camds_tab.stop_import)
        self.mode.currentTextChanged.connect(self.overview_tab.set_mode)
        self.mode.currentTextChanged.connect(lambda _: self._apply_state(self.state_machine.state.value))
        self.overview_tab.set_mode(self.mode.currentText())

    def _import_not_available(self) -> None:
        if self.mode.currentText() == "IMPORT_TREE":
            self.tabs.setCurrentWidget(self.camds_tab)
            self.camds_tab.review_import()
            return
        if self.mode.currentText() == "DRY_RUN" and self.document and self.source_path:
            output = Path("output") / self.source_path.stem / "dry_run_plan.json"
            plan = write_dry_run_plan(self.document.to_dict(), output)
            self.logs_tab.append("CAMDS", f"Dry-run complete: {len(plan)} planned operations; no CAMDS data changed")
            QMessageBox.information(self, "Dry Run Complete", f"{len(plan)} operations were written to:\n{output}")
            return
        QMessageBox.information(
            self,
            "CAMDS Import",
            "Parsed data is available in the MDS Tree. Use DRY_RUN for a local plan, or CAMDS Search / Create to search and prepare one unsaved root.",
        )

    def _camds_node_progress(self, event) -> None:
        """One import step drives every workflow indicator at once."""
        self.progress_tab.set_node_progress(event)
        self.progress.stage = ImportStage.CAMDS_IMPORTING
        self.progress.completed_nodes = event.completed
        self.progress.total_nodes = event.total
        self.progress.current_node_uid = event.uid
        self.progress.current_node_name = event.name
        self.progress.elapsed_seconds = event.elapsed_seconds
        self.stage_label.setText("Current stage: " + event.message)
        self.node_label.setText(f"Current node: {event.counter} — {event.name or '-'} (under {event.parent_path})")
        if event.total:
            self._set_overall(int(round(100 * event.completed / event.total)))
        if event.event == "paused":
            self.status_label.set("Status: Paused between steps", Lamp.WARN)
            if self.state_machine.state == AppState.IMPORTING:
                self.state_machine.transition(AppState.PAUSED)
        elif event.event in ("node_failed", "stopped"):
            self.progress.errors += 1
            self.logs_tab.append("ERROR", f"{event.kind or 'node'} {event.name or '-'}: {event.message}")
        elif self.state_machine.state == AppState.PAUSED:
            self.state_machine.transition(AppState.IMPORTING)
        self.progress_tab.update_progress(self.progress)

    def _camds_session_changed(self, status: str) -> None:
        """Connection, status, stage and progress always describe the same browser."""
        if status == "AUTHENTICATED":
            self.authenticated = True
            self.connection_label.set("CAMDS: Logged in (operations session)", Lamp.OK)
            self.overview_tab.set_connection("Authenticated", self.credentials.get_username() or "-")
            self._set_stage("CAMDS_AUTHENTICATED")
            self.progress.stage = ImportStage.CAMDS_AUTHENTICATED
            self.progress_tab.update_progress(self.progress)
            if AppState.CAMDS_AUTHENTICATED in self._allowed_states():
                self.state_machine.transition(AppState.CAMDS_AUTHENTICATED)
            self.logs_tab.append("CAMDS", "Operations browser is authenticated")
        elif status in ("EXPIRED", "LOGIN_REQUIRED"):
            self.authenticated = False
            self.connection_label.set(
                "CAMDS: Session expired" if status == "EXPIRED" else "CAMDS: Not connected",
                Lamp.WARN)
            self.overview_tab.set_connection("Not connected", "-")
            self._set_stage("CAMDS_LOGIN_REQUIRED")
            if AppState.CAMDS_LOGIN_REQUIRED in self._allowed_states():
                self.state_machine.transition(AppState.CAMDS_LOGIN_REQUIRED)
        else:
            self.authenticated = False
            self.connection_label.set("CAMDS: Not connected", Lamp.IDLE)

    def _camds_session_ready(self) -> None:
        credentials, self._pending_login = self._pending_login, None
        if credentials:
            self.camds_tab.sign_in(credentials)

    def _camds_operation_status(self, message: str, complete: bool) -> None:
        self.stage_label.setText("Current stage: CAMDS operation complete" if complete else "Current stage: " + message)
        self.node_label.setText(message)
        if complete:
            self.overall.setValue(100)
            self.status_label.set("Status: Ready", Lamp.OK)

    def _import_finished(self, result: object) -> None:
        elapsed = (time.monotonic() - self._import_started_at) if self._import_started_at else 0.0
        self._import_started_at = None
        title, body = summary(result, parse_seconds=self._parse_seconds,
                              import_seconds=elapsed,
                              nodes=getattr(self.statistics, "total_nodes", 0))
        self._announce(title, body, QMessageBox.Icon.Information)

    def _import_failed(self, message: str) -> None:
        elapsed = (time.monotonic() - self._import_started_at) if self._import_started_at else 0.0
        self._import_started_at = None
        journal = getattr(self.camds_tab, "journal_path", None)
        title, body = failure(message, parse_seconds=self._parse_seconds,
                              import_seconds=elapsed,
                              done=self.progress.completed_nodes, total=self.progress.total_nodes,
                              journal=journal)
        self._announce(title, body, QMessageBox.Icon.Warning)

    def _announce(self, title: str, body: str, icon) -> None:
        """Say it in the log, flash the taskbar, and put it on screen.

        An import runs for hours and the window is usually behind something
        else by the time it ends, so the taskbar entry is flashed: raising the
        window would steal focus from whatever the operator moved on to.
        """
        for line in body.splitlines():
            if line.strip():
                self.logs_tab.append("INFO", line)
        QApplication.alert(self)
        box = QMessageBox(icon, title, body, QMessageBox.StandardButton.Ok, self)
        box.exec()

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        import_action = QAction("Import IMDS PDF", self)
        import_action.triggered.connect(self.select_pdf)
        file_menu.addAction(import_action)
        excel_action = QAction("Export parsed data to Excel", self)
        excel_action.triggered.connect(self.export_excel_data)
        file_menu.addAction(excel_action)
        pdf_action = QAction("Export parsed data to PDF", self)
        pdf_action.triggered.connect(self.export_pdf_data)
        file_menu.addAction(pdf_action)
        camds_menu = self.menuBar().addMenu("CAMDS")
        operations_action = QAction("Search / Create", self)
        operations_action.triggered.connect(lambda: self.tabs.setCurrentWidget(self.camds_tab))
        camds_menu.addAction(operations_action)
        login_action = QAction("Login CAMDS", self)
        login_action.triggered.connect(self.login_from_menu)
        camds_menu.addAction(login_action)
        discover_action = QAction("Discover Authenticated Page", self)
        discover_action.triggered.connect(self.start_discovery)
        camds_menu.addAction(discover_action)
        # Maintenance rather than part of an import: it records the creation
        # wizard so classifications beyond 1.1.1 are supported.
        wizard_action = QAction("Record classification wizard", self)
        wizard_action.triggered.connect(self.camds_tab.discover_classifications)
        camds_menu.addAction(wizard_action)
        camds_menu.addSeparator()
        check_action = QAction("Check substances against the catalogue", self)
        check_action.triggered.connect(self.check_substances)
        camds_menu.addAction(check_action)
        self.menuBar().addMenu("Help")

    def select_pdf(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Import IMDS PDF", "", "PDF files (*.pdf)")
        if not filename:
            return
        self.source_path = Path(filename)
        try:
            with pymupdf.open(self.source_path) as pdf:
                pages = pdf.page_count
        except Exception as exc:
            QMessageBox.critical(self, "Invalid PDF", str(exc))
            return
        size_mb = self.source_path.stat().st_size / (1024 * 1024)
        self.file_label.setText(f"{self.source_path.name} ({size_mb:.1f} MB, {pages} pages)")
        self.overview_tab.set_source(self.source_path, pages)
        self._selected_page_count = pages
        self.state_machine.transition(AppState.DOCUMENT_LOADED)
        self.start_parse()

    def start_parse(self) -> None:
        if not self.source_path:
            return
        self.state_machine.transition(AppState.PARSING)
        self._parse_started_at = time.monotonic()
        self._set_overall_busy()
        self._set_stage("PDF_LOADING")
        self._page_progress(0, self._selected_page_count)
        self.node_label.setText("Current node: Reading PDF pages…")
        self.logs_tab.append("PARSER", "PDF parsing started; reading pages in background")
        worker = ParserWorker(self.source_path)
        thread = self._run_worker(worker)
        worker.progress_changed.connect(self._set_overall)
        worker.operation_progress_changed.connect(self.progress_tab.set_operation)
        worker.operation_progress_changed.connect(self._page_progress)
        worker.stage_changed.connect(self._set_stage)
        worker.log_message.connect(lambda message: self.logs_tab.append("PARSER", message))
        worker.error.connect(self._worker_error)
        worker.completed.connect(self._parse_completed)
        thread.start()

    def _parse_completed(self, document: MDSDocument, statistics: object) -> None:
        if self._parse_started_at is not None:
            self._parse_seconds = time.monotonic() - self._parse_started_at
        self.document, self.statistics = document, statistics
        self.camds_tab.set_document(document)
        self.state_machine.transition(AppState.PARSED)
        self.tree_tab.set_root(document.root)
        self.overview_tab.set_document(document, statistics)
        metadata = document.metadata
        weight = f"{metadata.weight_g:g} g" if metadata.weight_g is not None else "-"
        self.metadata_label.setText(f"IMDS ID: {metadata.imds_id or '-'} / {metadata.version or '-'}    Part No: {metadata.part_number or '-'}    Weight: {weight}")
        self.progress.total_nodes = statistics.total_nodes
        self.progress.total_components = statistics.components
        self.progress.total_materials = statistics.materials
        self.progress.total_substances = statistics.substances
        self.progress_tab.update_progress(self.progress)
        output = Path("output") / (self.source_path.stem if self.source_path else "document")
        output.mkdir(parents=True, exist_ok=True)
        (output / "mds.json").write_text(json.dumps(document.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        self.logs_tab.append("INFO", "PDF parsing and tree construction complete")

    def start_validation(self) -> None:
        if not self.document:
            return
        self.state_machine.transition(AppState.VALIDATING)
        worker = ValidationWorker(self.document.root)
        thread = self._run_worker(worker)
        worker.stage_changed.connect(self._set_stage)
        worker.progress_changed.connect(self._set_overall)
        worker.error.connect(self._worker_error)
        worker.completed.connect(self._validation_completed)
        thread.start()

    def _validation_completed(self, issues: list[object]) -> None:
        self.validation_tab.set_issues(issues)
        self.progress.warnings = sum(issue.severity == "WARNING" for issue in issues)
        self.progress.errors = sum(issue.severity == "ERROR" for issue in issues)
        self.progress_tab.update_progress(self.progress)
        if self.progress.errors:
            self.state_machine.transition(AppState.FAILED)
        else:
            self.state_machine.transition(AppState.READY)
        self.logs_tab.append("INFO", f"Validation complete: {self.progress.errors} errors, {self.progress.warnings} warnings")
        self.check_substances(after_validation=True)

    def check_substances(self, after_validation: bool = False) -> None:
        """Ask CAMDS whether it holds every substance the tree names.

        Offline validation cannot answer this, and an unresolved substance stops
        an import hours in. It is read-only and takes about a minute, so it runs
        as the last step of Validate whenever a session is available - and never
        silently: when it cannot run, the log says why.
        """
        reason = self.camds_tab.can_check_substances()
        if not reason:
            self.logs_tab.append("CAMDS", "Checking every substance against the CAMDS catalogue…")
            self.camds_tab.check_substances()
            return
        if after_validation:
            self.logs_tab.append(
                "INFO", "Substances were not checked against CAMDS (" + reason + "). "
                "Sign in and use CAMDS -> Check substances before a long import.")
        else:
            self.logs_tab.append("INFO", "Cannot check substances: " + reason + ".")

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.credentials, self)
        dialog.test_login_requested.connect(self.start_test_login)
        dialog.exec()

    def login_from_menu(self) -> None:
        """Sign in on the operations browser, never in a second throwaway session."""
        credentials = self.credentials.get_credentials()
        if not credentials:
            self.open_settings()
            credentials = self.credentials.get_credentials()
        if not credentials:
            return
        self.tabs.setCurrentWidget(self.camds_tab)
        self._set_stage("CAMDS_LOGIN")
        if self.camds_tab.worker is None:
            self._pending_login = credentials
            self.camds_tab.open_session()
        else:
            self.camds_tab.sign_in(credentials)

    def export_excel_data(self) -> None:
        if not self.document:
            QMessageBox.information(self, "Export", "Parse a PDF before exporting.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Excel", "mds-data.xlsx", "Excel workbook (*.xlsx)")
        if path:
            export_excel(self.document, Path(path))

    def export_pdf_data(self) -> None:
        if not self.document:
            QMessageBox.information(self, "Export", "Parse a PDF before exporting.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", "mds-data.pdf", "PDF file (*.pdf)")
        if path:
            export_pdf(self.document, Path(path))

    def start_test_login(self, credentials: Credentials | None = None) -> None:
        resolved = credentials or self.credentials.get_credentials()
        if not resolved:
            QMessageBox.warning(self, "CAMDS Account", "Enter CAMDS credentials in Settings first.")
            return
        if self.state_machine.state != AppState.CAMDS_LOGIN_REQUIRED and AppState.CAMDS_LOGIN_REQUIRED in self._allowed_states():
            self.state_machine.transition(AppState.CAMDS_LOGIN_REQUIRED)
        config = BrowserConfig(
            login_url="https://catarc.camds.org.cn/#/login",
            storage_state_path=Path(".runtime/camds_storage_state.json"),
            headless=False,
        )
        worker = CamdsLoginWorker(CamdsBrowser(config), resolved)
        self._active_login_worker = worker
        thread = self._run_worker(worker)
        worker.stage_changed.connect(self._login_stage)
        worker.error.connect(self._worker_error)
        worker.completed.connect(lambda result: self._login_completed(result, resolved.username))
        thread.start()

    def start_discovery(self) -> None:
        storage = Path(".runtime/camds_storage_state.json")
        worker = DiscoveryWorker(
            CamdsBrowser(BrowserConfig(login_url="https://catarc.camds.org.cn/#/login", storage_state_path=storage, headless=False)),
            Path("debug") / "authenticated-home",
            "https://catarc.camds.org.cn/",
        )
        thread = self._run_worker(worker)
        worker.completed.connect(lambda result: self.logs_tab.append("CAMDS", f"Discovery snapshot saved: {result.get('url', '-') }"))
        worker.error.connect(self._worker_error)
        thread.start()

    def _login_stage(self, stage: str) -> None:
        if self.state_machine.state == AppState.PARSING:
            return
        self._set_stage(stage)
        if stage == "CAMDS_WAITING_VERIFICATION":
              self.connection_label.set("CAMDS: Waiting verification", Lamp.BUSY)
              self.logs_tab.append("WAIT", "Complete slider/CAPTCHA in the CAMDS browser window")

    def _login_completed(self, result: object, username: str) -> None:
        """Report a credentials test, which is not the operations session.

        This runs in a second browser of its own, so it proves the username and
        password and nothing else. It used to light the connection lamp green
        and set `authenticated`, which said the import had a session when the
        operations browser was still anonymous. That lamp only ever follows the
        operations browser now, which polls the live page and says so itself.

        The sign-in is not wasted: it writes the shared storage state, so the
        next operations browser opens already signed in.
        """
        if result.status == LoginStatus.AUTHENTICATED:
            self.logs_tab.append(
                "CAMDS", f"Credentials accepted for {username} in a test browser "
                f"({result.url}). This is not the operations session: use CAMDS -> Login "
                "CAMDS, or Open CAMDS browser, before importing.")
        else:
            self.logs_tab.append("ERROR", result.message)

    def _run_worker(self, worker: QObject) -> QThread:
        thread = WorkerThread(worker, self)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._threads.remove(thread) if thread in self._threads else None)
        self._threads.append(thread)
        return thread

    def _set_overall(self, value: int) -> None:
        if self.overall.maximum() == 0:
            self.overall.setRange(0, 100)
        self.overall.setValue(value)
        self.progress_tab.set_overall(value)

    def _set_overall_busy(self) -> None:
        self.overall.setRange(0, 0)
        self.progress_tab.begin_busy()

    def _page_progress(self, current: int, total: int) -> None:
        elapsed = time.monotonic() - self._parse_started_at if self._parse_started_at else 0.0
        self.progress.elapsed_seconds = elapsed
        if current > 0 and elapsed > 0:
            rate = current / elapsed
            remaining = max(total - current, 0)
            eta = remaining / rate if rate else 0.0
            self.progress_tab.labels["Rate"].setText(f"{rate:.2f} pages/s")
            self.progress_tab.labels["ETA"].setText(self._format_duration(eta))
        else:
            self.progress_tab.labels["Rate"].setText("Calculating…")
            self.progress_tab.labels["ETA"].setText("Calculating…")
        self.progress_tab.labels["Completed"].setText(f"Reading PDF page {current} / {total}")
        self.progress_tab.labels["Current item"].setText(f"Page {current} of {total}")
        self.node_label.setText(f"Current node: Reading PDF page {current} / {total} ({max(total - current, 0)} remaining)")

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds <= 0:
            return "< 1 s"
        whole_seconds = int(seconds)
        minutes, remainder = divmod(whole_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"~{hours}h {minutes}m remaining"
        if minutes:
            return f"~{minutes}m {remainder}s remaining"
        return f"~{remainder}s remaining"

    def _set_stage(self, stage: str) -> None:
        readable = stage.replace("_", " ").title()
        self.stage_label.setText(f"Current stage: {readable}")
        self.progress_tab.set_stage(stage)

    def _worker_error(self, message: str) -> None:
        self.logs_tab.append("ERROR", message)
        self.status_label.set("Status: Failed", Lamp.ERROR)
        if AppState.FAILED in self._allowed_states():
            self.state_machine.transition(AppState.FAILED)
        # The dialog shows the reason; the Logs tab keeps the whole text.
        QMessageBox.critical(self, "Operation failed", brief(message, lines=8, width=1200))

    def _allowed_states(self) -> set[AppState]:
        from ..core.state_machine import ALLOWED_TRANSITIONS
        return ALLOWED_TRANSITIONS[self.state_machine.state]

    def _apply_state(self, state_value: str) -> None:
        state = AppState(state_value)
        self.parse_button.setEnabled(state == AppState.DOCUMENT_LOADED)
        self.validate_button.setEnabled(state == AppState.PARSED)
        ready_data = self.document is not None and state in {AppState.READY, AppState.CAMDS_AUTHENTICATED}
        self.start_button.setEnabled(ready_data)
        self.start_button.setText("Start Import")
        importing = self.camds_tab.importing
        control = getattr(self.camds_tab.worker, "control", None)
        self.pause_button.setEnabled(importing and control is not None and not control.paused)
        self.resume_button.setEnabled(importing and control is not None and control.paused)
        self.stop_button.setEnabled(importing and control is not None and not control.stopping)
        # A paused import must stay visible; a later state change must not erase it.
        if importing and control is not None and control.paused:
            self.status_label.set("Status: Paused between steps", Lamp.WARN)
            self._set_stage("PAUSED")
        else:
            self.status_label.set(f"Status: {state.value.replace('_', ' ').title()}",
                                  LAMP_FOR_STATE.get(state, Lamp.BUSY))
            if state in RESTING_STATES:
                # While work is running the worker narrates the finer stage; once
                # it stops, the state is the only thing left that is true.
                self._set_stage(state.value)

    def closeEvent(self, event) -> None:
        # Cancel Playwright on its own event loop before its QThread is destroyed.
        if self.camds_tab.worker is not None:
            self.camds_tab.stop_session()
            event.ignore()
            QTimer.singleShot(150, self.close)
            return
        super().closeEvent(event)
