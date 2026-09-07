"""Review parsed-tree mapping before any external Create/Save operation."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QVBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QPlainTextEdit, QPushButton, QHBoxLayout,
)

from ..camds.application_mapping import ApplicationMapping
from ..camds.import_plan import ImportRequest


def _candidates(root, depth=0):
    """Nodes that can start a transfer, with their depth and subtree size."""
    def size(node):
        return 1 + sum(size(child) for child in node.get("children", []))

    found = []

    def visit(node, level):
        if node.get("node_type") in ("COMPONENT", "SEMICOMPONENT", "MATERIAL"):
            found.append((node, level, size(node)))
        for child in node.get("children", []):
            visit(child, level + 1)

    visit(root, depth)
    return found


class ImportDialog(QDialog):
    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review parsed tree → CAMDS draft")
        self.resize(950, 650)
        self.document_root = root
        self.root = root
        self.request = None
        self.mapping = ApplicationMapping()
        layout = QVBoxLayout(self)
        intro = QLabel("Create and Save the parsed tree as CAMDS drafts. Leave both reference cells empty to create a new Material in its own classification, or enter an existing CAMDS ID and exact version. IMDS IDs are not CAMDS IDs. Save happens after every change; no Send/Submit.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        # A report can carry one unsupported branch and many importable ones, so
        # the transfer starts from a chosen node rather than always from the root.
        picker = QHBoxLayout()
        picker.addWidget(QLabel("Import starting at:"))
        self.subtree = QComboBox()
        self.subtree.setEditable(True)
        self.subtree.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.subtree.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.subtree.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        for node, depth, size in _candidates(root):
            self.subtree.addItem(f"{'    ' * depth}{node['node_type']}: {node['name']} ({size} nodes)", node)
        picker.addWidget(self.subtree, 1)
        layout.addLayout(picker)
        self.materials = []
        self.mapping_table = QTableWidget(0, 5)
        self.mapping_table.setHorizontalHeaderLabels(["Parsed Material", "Classification", "Mass (g)", "Existing CAMDS ID", "Version"])
        layout.addWidget(self.mapping_table)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview)
        self.reuse = QCheckBox(
            "Reuse Materials already in CAMDS instead of creating another "
            "(same name, same substances, same portions; only whole-numbered versions)")
        self.reuse.setChecked(True)
        self.reuse.setToolTip(
            "Creating a Material per run is what fills an account with duplicates of the "
            "same thing. A name is not an identity - one report calls two Materials "
            "\"Ep-Ni\" - so the composition decides. Unchecked, every Material in the "
            "report is created afresh.")
        layout.addWidget(self.reuse)
        self.release = QCheckBox(
            "Release each Material this run creates (publishes it in CAMDS)")
        self.release.setChecked(False)
        self.release.setToolTip(
            "Publishing is outward-facing and cannot be undone from here. Only Materials "
            "this run created are released; a reused or mapped one is somebody else's. "
            "CAMDS validates first, and a Material it reports errors on is not published.")
        layout.addWidget(self.release)
        self.resume = QCheckBox(
            "Resume an interrupted run of this exact tree and mapping "
            "(skips Materials already verified; refuses if a draft editor was left half-built)")
        self.resume.setChecked(False)
        layout.addWidget(self.resume)
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
        self.mapping_table.itemChanged.connect(self.invalidate)
        self.subtree.currentIndexChanged.connect(self.load_subtree)
        self.load_subtree()

    def load_subtree(self, *_):
        """Point the review at the chosen node and rebuild its Material table."""
        node = self.subtree.currentData()
        self.root = node if node is not None else self.document_root
        self.materials = ImportRequest(self.root).materials()
        self.mapping_table.blockSignals(True)
        self.mapping_table.setRowCount(len(self.materials))
        for row, material in enumerate(self.materials):
            for col, value in enumerate((material["name"], material.get("classification") or "",
                                         str(material.get("weight_g") or ""), "", "")):
                item = QTableWidgetItem(value)
                if col < 3:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.mapping_table.setItem(row, col, item)
        self.mapping_table.blockSignals(False)
        self.mapping_table.resizeColumnsToContents()
        self.validate_plan()

    def invalidate(self, *_):
        self.request = None
        self.start.setEnabled(False)

    def validate_plan(self):
        refs = {}
        for row, node in enumerate(self.materials):
            ref = tuple(self.mapping_table.item(row, col).text().strip() for col in (3, 4))
            if any(ref):
                refs[node["uid"]] = ref
        request = ImportRequest(self.root, refs).snapshot()
        try:
            warnings = request.validate(self.mapping)
        except (ValueError, TypeError) as exc:
            self.preview.setPlainText(str(exc))
            self.invalidate()
            return
        lines = ["Ready for draft transfer. Existing material composition will be reused, not overwritten.",
                 "Journal prevents automatic replay after failure. No automatic rollback/delete."]
        # Accepted as declared, but the operator has to see them before starting.
        if request.merges:
            lines.append("")
            lines.append(f"Combined {len(request.merges)} repeated substance entries:")
            lines.extend("  " + note for note in request.merges[:20])
            if len(request.merges) > 20:
                lines.append(f"  ... and {len(request.merges) - 20} more")
        if request.derived:
            lines.append("")
            # A value the report did not state is the one thing here that is
            # ours rather than the supplier's, so it is listed on its own.
            lines.append(f"{len(request.derived)} mass(es) worked out from what the node contains, "
                         "because IMDS printed none:")
            lines.extend("  " + note for note in request.derived[:20])
            if len(request.derived) > 20:
                lines.append(f"  ... and {len(request.derived) - 20} more")
        unmapped = self.mapping.missing(request.root)
        if unmapped:
            lines.append("")
            # Preflight cannot see the options CAMDS will offer, so the choice
            # is made during the run, and an unclear one is left unset.
            lines.append(f"{len(unmapped)} application(s) taken from the report. Each is matched against "
                         "the options CAMDS offers for that substance; anything that does not match "
                         "exactly is left unset and listed when the run finishes:")
            lines.extend(f"  {sub}: {text}" for sub, text in unmapped[:20])
            if len(unmapped) > 20:
                lines.append(f"  ... and {len(unmapped) - 20} more")
        if warnings:
            lines.append("")
            lines.append(f"{len(warnings)} item(s) imported as declared, review them in CAMDS afterwards:")
            lines.extend("  " + note for note in warnings[:20])
            if len(warnings) > 20:
                lines.append(f"  ... and {len(warnings) - 20} more")
        lines.append("")
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
