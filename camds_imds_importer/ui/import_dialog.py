"""Review parsed-tree mapping before any external Create/Save operation."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QVBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QPlainTextEdit, QPushButton, QHBoxLayout,
)

from ..camds.application_mapping import ApplicationMapping
from ..camds.import_plan import ImportRequest
from ..camds.material_classifications import describe, known_codes, needs_choice, sort_key


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
        intro = QLabel("Create and Save the parsed tree as CAMDS drafts. Leave both reference cells empty to create a new Material in its own classification, or enter an existing CAMDS ID and exact version. IMDS IDs are not CAMDS IDs. Where the report printed no classification, choose one in the Classification column. Save happens after every change; no Send/Submit.")
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
        self.class_choice: dict[int, QComboBox] = {}   # row -> chooser, where IMDS printed none
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
        self.by_number = QCheckBox(
            "Attach any Component already in CAMDS with the same Component number, "
            "without building what is inside it")
        self.by_number.setChecked(False)
        self.by_number.setToolTip(
            "The number is taken as the identity: what the report says the part contains is "
            "not compared with what CAMDS says it contains, and nothing inside a matched "
            "Component is created - its Materials are never made. Right when CAMDS is already "
            "the authority on that part; wrong if the report describes something the number no "
            "longer means. Unchecked, a Component is reused only when its Part No., its number "
            "of children and every child's MDS all agree.")
        layout.addWidget(self.by_number)
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
        self.class_choice = {}
        # Rebuild from empty: shrinking the table would leave the choosers of
        # the branch just left behind, attached to rows that now mean something
        # else.
        self.mapping_table.setRowCount(0)
        self.mapping_table.setRowCount(len(self.materials))
        for row, material in enumerate(self.materials):
            for col, value in enumerate((material["name"], material.get("classification") or "",
                                         str(material.get("weight_g") or ""), "", "")):
                item = QTableWidgetItem(value)
                if col < 3:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.mapping_table.setItem(row, col, item)
            if needs_choice(material.get("classification")):
                self._offer_classification(row, material)
        self.mapping_table.blockSignals(False)
        self.mapping_table.resizeColumnsToContents()
        self.validate_plan()

    def _offer_classification(self, row, material):
        """Let the operator say what a Material is when the report did not.

        Two label-paper rows with an empty classification cell stopped an
        otherwise clean 4293-node import, and the only ways out were editing
        the parsed data by hand or pointing the row at some existing CAMDS
        Material it is not. The class is a fact about the material that the
        operator knows and the report failed to print, so it is asked for here
        rather than guessed during the run.
        """
        choice = QComboBox()
        choice.addItem("choose a classification", None)
        for code in sorted(known_codes(), key=sort_key):
            text = f"{code}: {describe(code)}" if describe(code) else code
            choice.addItem(text, code)
        stated = material.get("classification")
        choice.setToolTip(
            f"The report printed {stated!r} for this Material, which is not a classification "
            "CAMDS was seen to offer." if stated else
            "The report printed no classification for this Material. CAMDS asks for one when "
            "a Material is created, and the importer will not guess it.")
        choice.currentIndexChanged.connect(self.validate_plan)
        self.class_choice[row] = choice
        self.mapping_table.setCellWidget(row, 1, choice)

    def invalidate(self, *_):
        self.request = None
        self.start.setEnabled(False)

    def validate_plan(self):
        refs = {}
        for row, node in enumerate(self.materials):
            ref = tuple(self.mapping_table.item(row, col).text().strip() for col in (3, 4))
            if any(ref):
                refs[node["uid"]] = ref
        chosen = {self.materials[row]["uid"]: choice.currentData()
                  for row, choice in self.class_choice.items() if choice.currentData()}
        request = ImportRequest(self.root, refs, chosen).snapshot()
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
        if request.chosen:
            lines.append("")
            # Somebody's decision, not the report's statement, so it is shown
            # apart from everything the supplier declared.
            lines.append(f"{len(request.chosen)} classification(s) chosen here, because IMDS "
                         "printed none:")
            lines.extend("  " + note for note in request.chosen)
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
