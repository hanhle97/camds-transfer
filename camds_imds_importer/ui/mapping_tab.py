from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class MappingTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Canonical-to-CAMDS mapping will be enabled during the dry-run iteration."))
        layout.addStretch()
