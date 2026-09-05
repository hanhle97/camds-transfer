from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QComboBox,
    QLineEdit, QPlainTextEdit, QPushButton, QLabel, QTableWidget,
    QTableWidgetItem, QAbstractItemView,
)

from ..camds.operations import SearchRequest, CreateRequest, KINDS
from ..workers.operations_worker import OperationsWorker


class CamdsTab(QWidget):
    log_message = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.worker = None
        self.busy = False
        self.editor_open = False
        self.last_error = ""
        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.open_button = QPushButton("Open CAMDS browser")
        self.close_button = QPushButton("Close browser session")
        bar.addWidget(self.open_button)
        bar.addWidget(self.close_button)
        root.addLayout(bar)
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
        create_group = QGroupBox("Prepare one MDS root — not saved")
        create_form = QFormLayout(create_group)
        self.create_kind = QComboBox()
        self.create_kind.addItems(KINDS[:3])
        self.create_name, self.create_number, self.create_mass = (QLineEdit() for _ in range(3))
        self.create_classification = QComboBox()
        self.create_classification.addItems(("", "1.1.1"))
        self.create_remark = QPlainTextEdit()
        self.create_remark.setMaximumHeight(65)
        self.source_node = QComboBox()
        self.source_node.addItem("No parsed IMDS document", None)
        self.load_node_button = QPushButton("Use selected IMDS node")
        for label, widget in (("IMDS node", self.source_node), ("", self.load_node_button), ("Type", self.create_kind), ("Name", self.create_name), ("Part / Material No.", self.create_number), ("Mass (g)", self.create_mass), ("Material classification", self.create_classification), ("Remark", self.create_remark)):
            create_form.addRow(label, widget)
        self.create_notice = QLabel("CAMDS allocates an ID when Create opens. This fills one root only; children, Save, Send and Submit are not automated. Review the form in the browser before closing.")
        self.create_notice.setWordWrap(True)
        create_form.addRow(self.create_notice)
        self.create_button = QPushButton("Create and fill root (without Save)")
        create_form.addRow(self.create_button)
        forms_layout.addWidget(create_group)
        root.addWidget(self.forms)
        self.results = QTableWidget()
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        root.addWidget(self.results)
        self.open_button.clicked.connect(self.open_session)
        self.close_button.clicked.connect(self.stop_session)
        self.search_button.clicked.connect(self.search)
        self.create_button.clicked.connect(self.create)
        self.load_node_button.clicked.connect(self.load_node)
        self.search_kind.currentTextChanged.connect(self._kind_changed)
        self.create_kind.currentTextChanged.connect(self._kind_changed)
        self._kind_changed()
        self._update()

    def _kind_changed(self, *_args) -> None:
        substance = self.search_kind.currentText() == "Basic Substance"
        self.search_cas.setEnabled(substance)
        self.search_id.setEnabled(not substance)
        self.search_number.setEnabled(not substance)
        self.search_source.setEnabled(not substance)
        self.create_mass.setEnabled(self.create_kind.currentText() == "Component")
        self.create_classification.setEnabled(self.create_kind.currentText() == "Material")

    def set_document(self, document) -> None:
        self.source_node.clear()
        def visit(node):
            if node.node_type.value in ("COMPONENT", "SEMICOMPONENT", "MATERIAL"):
                self.source_node.addItem(f"{node.node_type.value}: {node.name}", node.to_dict())
            for child in node.children:
                visit(child)
        visit(document.root)

    def load_node(self) -> None:
        node = self.source_node.currentData()
        if not node:
            return
        kind = {"COMPONENT": "Component", "SEMICOMPONENT": "Semicomponent", "MATERIAL": "Material"}[node["node_type"]]
        self.create_kind.setCurrentText(kind)
        self.create_name.setText(node["name"])
        self.create_number.setText((node.get("material_number") if kind == "Material" else node.get("part_number")) or "")
        self.create_mass.setText(str(node["weight_g"]) if node.get("weight_g") is not None else "")
        classification = node.get("classification") or ""
        self.create_classification.setCurrentIndex(1 if classification == "1.1.1" else 0)
        self.create_remark.clear()
        self.status.setText("IMDS node loaded for review. Only this root will be filled; its children are not imported." + (" Material classification is unsupported; automatic Create is unavailable for this classification." if kind == "Material" and classification != "1.1.1" else ""))

    def open_session(self) -> None:
        if self.worker is not None:
            return
        self.worker = OperationsWorker(Path(".runtime/camds_storage_state.json"), self)
        self.last_error = ""
        self.worker.ready.connect(self._ready)
        self.worker.operation_progress.connect(self._progress)
        self.worker.result.connect(self._result)
        self.worker.failed.connect(self._failed)
        self.worker.session_error.connect(lambda message: self._failed(message, self.editor_open))
        self.worker.finished.connect(self._finished)
        self.busy = True
        self.status.setText("Opening CAMDS browser…")
        self._update()
        self.worker.start()

    def stop_session(self) -> None:
        if self.worker:
            self.worker.stop()
            self.busy = True
            self.status.setText("Closing browser. Any unsaved form will not be preserved.")
            self._update()

    def _ready(self) -> None:
        if self.worker is None or self.worker.stopping.is_set():
            return
        self.busy = False
        self.status.setText("Browser open. Complete login/slider there if needed and use English, then run Search or Create.")
        self._update()

    def _progress(self, message: str) -> None:
        self.busy = True
        self.status.setText(message + " Keep the CAMDS browser open…")
        self.log_message.emit(message)
        self._update()

    def _submit(self, action, request) -> None:
        if not self.worker or self.busy or self.editor_open:
            return
        try:
            request.validate()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.busy = True
        self.last_error = ""
        self.results.setRowCount(0)
        self.status.setText("Searching CAMDS…" if action == "search" else "Opening and filling a new root. Keep the browser on this page…")
        self.worker.submit(action, request)
        self._update()

    def search(self) -> None:
        substance = self.search_kind.currentText() == "Basic Substance"
        self._submit("search", SearchRequest(self.search_kind.currentText(), self.search_name.text().strip(), "" if substance else self.search_id.text().strip(), "" if substance else self.search_number.text().strip(), self.search_cas.text().strip() if substance else "", self.search_source.currentText()))

    def create(self) -> None:
        kind = self.create_kind.currentText()
        self._submit("create", CreateRequest(kind, self.create_name.text().strip(), self.create_number.text().strip(), self.create_mass.text().strip() if kind == "Component" else "", self.create_classification.currentText() if kind == "Material" else "", self.create_remark.toPlainText()))

    def _result(self, result) -> None:
        self.busy = False
        self.editor_open = result["kind"] == "create"
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
        self._update()

    def _failed(self, message, editor_open) -> None:
        self.busy = False
        self.editor_open = editor_open
        suffix = " The editor may contain a partially filled root. Review it; no automatic retry or Save was performed." if editor_open else ""
        self.last_error = message + suffix
        self.status.setText(message + suffix)
        self.log_message.emit(message + suffix)
        self._update()

    def _finished(self) -> None:
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self.busy = False
        self.editor_open = False
        self.status.setText((self.last_error + " " if self.last_error else "") + "Browser session closed. Open a new session to continue.")
        self._update()

    def _update(self) -> None:
        stopping = self.worker is not None and self.worker.stopping.is_set()
        self.open_button.setEnabled(self.worker is None)
        self.close_button.setEnabled(self.worker is not None and not stopping)
        self.forms.setEnabled(self.worker is not None and not stopping and not self.busy and not self.editor_open)
