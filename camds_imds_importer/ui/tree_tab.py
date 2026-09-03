from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QTreeView, QVBoxLayout, QWidget

from ..parser.models import MDSNode


class TreeTab(QWidget):
    HEADERS = ("Status", "Level", "Type", "Name", "Part No.", "CAS", "Quantity", "Weight", "%", "Classification", "CAMDS Match")

    def __init__(self) -> None:
        super().__init__()
        self.view = QTreeView()
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(self.HEADERS)
        self.view.setModel(self.model)
        self.view.setAlternatingRowColors(True)
        self.view.setUniformRowHeights(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.view)

    def set_root(self, root: MDSNode) -> None:
        self.model.removeRows(0, self.model.rowCount())

        def items(node: MDSNode) -> list[QStandardItem]:
            percentage = f"{node.percentage:g}" if node.percentage is not None else (
                f"{node.percentage_min:g}-{node.percentage_max:g}" if node.percentage_min is not None and node.percentage_max is not None else ""
            )
            values = ("○ Pending", str(node.level), node.node_type.value, node.name, node.part_number or node.material_number or "", node.cas_number or "", f"{node.quantity:g}" if node.quantity is not None else "", f"{node.weight_g:g}" if node.weight_g is not None else "", percentage, node.classification or "", "Not processed")
            result = [QStandardItem(value) for value in values]
            result[0].setData(node.uid, Qt.ItemDataRole.UserRole)
            return result

        def add(parent: QStandardItem, node: MDSNode) -> None:
            row = items(node)
            parent.appendRow(row)
            for child in node.children:
                add(row[0], child)

        add(self.model.invisibleRootItem(), root)
        self.view.expandToDepth(2)
        self.view.resizeColumnToContents(0)
        self.view.resizeColumnToContents(3)
