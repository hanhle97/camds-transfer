from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QComboBox, QPlainTextEdit, QVBoxLayout, QWidget


class LogsTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.filter = QComboBox()
        self.filter.addItems(("ALL", "INFO", "WARNING", "ERROR", "CAMDS", "PARSER"))
        self.viewer = QPlainTextEdit()
        self.viewer.setReadOnly(True)
        layout.addWidget(self.filter)
        layout.addWidget(self.viewer)

    def append(self, level: str, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.viewer.appendPlainText(f"{timestamp} {level:<7} {message}")
