"""Review parsed-tree mapping before any external Create/Save operation."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QPlainTextEdit, QPushButton, QHBoxLayout,
)

from ..camds.import_plan import ImportRequest


class ImportDialog(QDialog):
    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review parsed tree → CAMDS draft")
        self.resize(950, 650)
        self.root = root
        self.request = None
        layout = QVBoxLayout(self)
        intro = QLabel("Create and Save the parsed tree as CAMDS drafts. Leave both reference cells empty to create a new Material (currently classification 1.1.1 only), or enter an existing CAMDS ID and exact version. IMDS IDs are not CAMDS IDs. Save happens after every change; no Send/Submit.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.materials = ImportRequest(root).materials()
        self.mapping = QTableWidget(len(self.materials), 5)
        self.mapping.setHorizontalHeaderLabels(["Parsed Material", "Classification", "Mass (g)", "Existing CAMDS ID", "Version"])
        for row, node in enumerate(self.materials):
            for col, value in enumerate((node["name"], node.get("classification") or "", str(node.get("weight_g") or ""), "", "")):
                item = QTableWidgetItem(value)
                if col < 3:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.mapping.setItem(row, col, item)
        self.mapping.resizeColumnsToContents()
        layout.addWidget(self.mapping)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)
        buttons = QHBoxLayout()
        self.check = QPushButton("Validate and preview")
        self.start = QPushButton("Create + Save tree in CAMDS")
        self.start.setEnabled(False)
        cancel = QPushButton("Cancel")
        for button in (self.check, self.start, cancel):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.check.clicked.connect(self.validate_plan)
        self.start.clicked.connect(self.transfer)
        cancel.clicked.connect(self.reject)
        self.mapping.itemChanged.connect(self.invalidate)
        self.validate_plan()

    def invalidate(self, *_):
        self.request = None
        self.start.setEnabled(False)

    def validate_plan(self):
        refs = {}
        for row, node in enumerate(self.materials):
            ref = tuple(self.mapping.item(row, col).text().strip() for col in (3, 4))
            if any(ref):
                refs[node["uid"]] = ref
        request = ImportRequest(self.root, refs).snapshot()
        try:
            request.validate()
        except (ValueError, TypeError) as exc:
            self.preview.setPlainText(str(exc))
            self.invalidate()
            return
        lines = ["Ready for draft transfer. Existing material composition will be reused, not overwritten.",
                 "Journal prevents automatic replay after failure. No automatic rollback/delete."]
        for mat in request.materials():
            if mat["uid"] in refs:
                lines.append(f"REUSE {mat['name']}: {'/'.join(refs[mat['uid']])}")
            else:
                lines.append(f"CREATE + SAVE Material {mat['name']}; add {len(mat['children'])} Substance(s), Save each")
        def show(node, depth=0):
            lines.append("  " * depth + f"{node['node_type']} {node['name']} | g={node.get('weight_g')} | qty={node.get('quantity')}")
            if node["uid"] not in refs:
                for child in node.get("children", []):
                    show(child, depth+1)
        show(request.root)
        lines.append("Finish: Search saved IDs → View → verify names, masses, quantities, references and new-material percentages.")
        self.preview.setPlainText("\n".join(lines))
        self.request = request
        self.start.setEnabled(True)

    def transfer(self):
        self.validate_plan()
        if self.request:
            self.accept()
