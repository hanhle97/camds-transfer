from __future__ import annotations

import json
import time
from pathlib import Path

import pymupdf
from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from ..camds.browser import BrowserConfig, CamdsBrowser
from ..camds.login import LoginStatus
from ..core.credentials import CredentialManager, Credentials
from ..core.progress import ImportProgress
from ..core.state_machine import AppState, ApplicationStateMachine
from ..parser.models import MDSDocument
from ..workers.camds_worker import CamdsLoginWorker
from ..workers.parser_worker import ParserWorker
from ..workers.validation_worker import ValidationWorker
from .logs_tab import LogsTab
from .mapping_tab import MappingTab
from .overview_tab import OverviewTab
from .progress_tab import ProgressTab
from .captcha_dialog import CaptchaDialog
from .settings_dialog import SettingsDialog
from .tree_tab import TreeTab
from .validation_tab import ValidationTab
from ..camds.dry_run import write_dry_run_plan


class WorkerThread(QThread):
    """Run a QObject worker explicitly; avoids relying on QThread.started delivery."""

    def __init__(self, worker: QObject, parent=None) -> None:
        super().__init__(parent)
        self.worker = worker
        worker.moveToThread(self)

    def run(self) -> None:
        self.worker.run()


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
        self.captcha_dialog: CaptchaDialog | None = None
        self._active_login_worker: CamdsLoginWorker | None = None
        self._parse_started_at: float | None = None
        self._selected_page_count: int = 0
        self._build_ui()
        self._build_menu()
        self.state_machine.state_changed.connect(self._apply_state)
        self._apply_state(self.state_machine.state.value)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        summary = QGroupBox("Workflow")
        grid = QGridLayout(summary)
        self.import_button = QPushButton("1. Import IMDS PDF")
        self.file_label = QLabel("No PDF selected")
        self.metadata_label = QLabel("IMDS ID: -    Part No: -    Weight: -")
        self.status_label = QLabel("● Status: Ready")
        self.connection_label = QLabel("● CAMDS: Not connected")
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
        self.login_button = QPushButton("Login CAMDS")
        self.start_button = QPushButton("Start Import")
        self.pause_button = QPushButton("Pause")
        self.resume_button = QPushButton("Resume")
        self.stop_button = QPushButton("Stop")
        self.mode = QComboBox()
        self.mode.addItems(("PARSE_ONLY", "DRY_RUN", "SAVE_DRAFT", "SUBMIT"))
        self.mode.setCurrentText("SAVE_DRAFT")
        buttons.addWidget(QLabel("Mode:"))
        buttons.addWidget(self.mode)
        for button in (self.parse_button, self.validate_button, self.login_button, self.start_button, self.pause_button, self.resume_button, self.stop_button):
            buttons.addWidget(button)
        grid.addLayout(buttons, 5, 0, 1, 4)
        root.addWidget(summary)
        self.tabs = QTabWidget()
        self.overview_tab = OverviewTab()
        self.tree_tab = TreeTab()
        self.validation_tab = ValidationTab()
        self.mapping_tab = MappingTab()
        self.progress_tab = ProgressTab()
        self.logs_tab = LogsTab()
        for title, widget in (("Overview", self.overview_tab), ("MDS Tree", self.tree_tab), ("Validation", self.validation_tab), ("CAMDS Mapping", self.mapping_tab), ("Progress", self.progress_tab), ("Logs", self.logs_tab)):
            self.tabs.addTab(widget, title)
        root.addWidget(self.tabs)
        self.setCentralWidget(central)
        self.import_button.clicked.connect(self.select_pdf)
        self.parse_button.clicked.connect(self.start_parse)
        self.validate_button.clicked.connect(self.start_validation)
        self.login_button.clicked.connect(self.open_settings)
        self.start_button.clicked.connect(self._import_not_available)
        self.mode.currentTextChanged.connect(self.overview_tab.set_mode)
        self.overview_tab.set_mode(self.mode.currentText())

    def _import_not_available(self) -> None:
        if self.mode.currentText() == "DRY_RUN" and self.document and self.source_path:
            output = Path("output") / self.source_path.stem / "dry_run_plan.json"
            plan = write_dry_run_plan(self.document.to_dict(), output)
            self.logs_tab.append("CAMDS", f"Dry-run complete: {len(plan)} planned operations; no CAMDS data changed")
            QMessageBox.information(self, "Dry Run Complete", f"{len(plan)} operations were written to:\n{output}")
            return
        QMessageBox.information(
            self,
            "CAMDS Import",
            "CAMDS data creation is intentionally disabled in this iteration. Test Login is available; dry-run import is the next implementation step.",
        )

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        import_action = QAction("Import IMDS PDF", self)
        import_action.triggered.connect(self.select_pdf)
        file_menu.addAction(import_action)
        settings_menu = self.menuBar().addMenu("Settings")
        account_action = QAction("CAMDS Account", self)
        account_action.triggered.connect(self.open_settings)
        settings_menu.addAction(account_action)
        camds_menu = self.menuBar().addMenu("CAMDS")
        login_action = QAction("Test Login", self)
        login_action.triggered.connect(self.open_settings)
        camds_menu.addAction(login_action)
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
        self.document, self.statistics = document, statistics
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

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.credentials, self)
        dialog.test_login_requested.connect(self.start_test_login)
        dialog.exec()

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
        worker.verification_screenshot.connect(self._show_verification)
        worker.completed.connect(lambda result: self._login_completed(result, resolved.username))
        thread.start()

    def _login_stage(self, stage: str) -> None:
        if self.state_machine.state == AppState.PARSING:
            return
        self._set_stage(stage)
        if stage == "CAMDS_WAITING_VERIFICATION":
            self.connection_label.setText("◉ CAMDS: Waiting verification")
            self.logs_tab.append("WAIT", "CAMDS requires interactive verification; complete it in the browser")
            if self.captcha_dialog is None:
                self.captcha_dialog = CaptchaDialog(self)
                self.captcha_dialog.code_submitted.connect(self._submit_verification_code)
            self.captcha_dialog.show()

    def _show_verification(self, image: bytes) -> None:
        if self.captcha_dialog is None:
            self.captcha_dialog = CaptchaDialog(self)
            self.captcha_dialog.code_submitted.connect(self._submit_verification_code)
        self.captcha_dialog.set_image(image)
        self.captcha_dialog.show()
        self.captcha_dialog.raise_()

    def _submit_verification_code(self, code: str) -> None:
        if self._active_login_worker and code.strip():
            self._active_login_worker.set_verification_code(code)

    def _login_completed(self, result: object, username: str) -> None:
        if self.captcha_dialog:
            self.captcha_dialog.close()
        if result.status == LoginStatus.AUTHENTICATED:
            self.authenticated = True
            self.connection_label.setText("✓ CAMDS: Logged in")
            self.overview_tab.set_connection("Authenticated", username)
            current = self.state_machine.state
            if current in {AppState.NO_DOCUMENT, AppState.DOCUMENT_LOADED, AppState.PARSED}:
                self.state_machine.transition(AppState.CAMDS_AUTHENTICATED)
            elif current == AppState.READY:
                self.state_machine.transition(AppState.CAMDS_AUTHENTICATED)
            self.logs_tab.append("CAMDS", f"CAMDS login successful; authenticated URL: {result.url}")
        else:
            self.connection_label.setText("⚠ CAMDS: Not connected")
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
        self.status_label.setText("✕ Status: Failed")
        if AppState.FAILED in self._allowed_states():
            self.state_machine.transition(AppState.FAILED)
        QMessageBox.critical(self, "Operation failed", message)

    def _allowed_states(self) -> set[AppState]:
        from ..core.state_machine import ALLOWED_TRANSITIONS
        return ALLOWED_TRANSITIONS[self.state_machine.state]

    def _apply_state(self, state_value: str) -> None:
        state = AppState(state_value)
        self.parse_button.setEnabled(state == AppState.DOCUMENT_LOADED)
        self.validate_button.setEnabled(state == AppState.PARSED)
        self.login_button.setEnabled(state not in {AppState.PARSING, AppState.VALIDATING, AppState.IMPORTING})
        ready_data = self.document is not None and state in {AppState.READY, AppState.CAMDS_AUTHENTICATED}
        parse_only = self.mode.currentText() == "PARSE_ONLY"
        self.start_button.setEnabled(ready_data and (parse_only or self.authenticated))
        self.pause_button.setEnabled(state == AppState.IMPORTING)
        self.resume_button.setEnabled(state == AppState.PAUSED)
        self.stop_button.setEnabled(state in {AppState.IMPORTING, AppState.PAUSED})
        self.status_label.setText(f"● Status: {state.value.replace('_', ' ').title()}")
