from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFormLayout, QLabel, QWidget

from ..core.statistics import TreeStatistics
from ..parser.models import MDSDocument


class OverviewTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QFormLayout(self)
        self.values = {name: QLabel("-") for name in ("PDF", "Pages", "IMDS ID", "Part", "Weight", "Components", "Materials", "Substances", "CAMDS Account", "Connection", "Mode")}
        for name, label in self.values.items():
            label.setTextInteractionFlags(label.textInteractionFlags())
            layout.addRow(f"{name}:", label)

    def set_source(self, path: Path, pages: int) -> None:
        self.values["PDF"].setText(path.name)
        self.values["Pages"].setText(str(pages))

    def set_document(self, document: MDSDocument, statistics: TreeStatistics) -> None:
        metadata = document.metadata
        self.values["IMDS ID"].setText(f"{metadata.imds_id or '-'} / {metadata.version or '-'}")
        self.values["Part"].setText(metadata.part_number or "-")
        self.values["Weight"].setText(f"{metadata.weight_g:g} g" if metadata.weight_g is not None else "-")
        self.values["Components"].setText(str(statistics.components))
        self.values["Materials"].setText(str(statistics.materials))
        self.values["Substances"].setText(str(statistics.substances))

    def set_connection(self, status: str, username: str | None = None) -> None:
        self.values["Connection"].setText(status)
        self.values["CAMDS Account"].setText(username or "-")

    def set_mode(self, mode: str) -> None:
        self.values["Mode"].setText(mode.replace("_", " ").title())
