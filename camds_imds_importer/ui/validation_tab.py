from __future__ import annotations

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget


class ValidationTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Severity", "Node", "Message"))
        QVBoxLayout(self).addWidget(self.table)

    def set_issues(self, issues: list[object]) -> None:
        self.table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            for column, value in enumerate((issue.severity, issue.node_uid, issue.message)):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))
