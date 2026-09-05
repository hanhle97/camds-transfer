from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QProgressBar, QVBoxLayout, QWidget

from ..core.progress import ImportProgress


class ProgressTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.overall = QProgressBar()
        self.current = QProgressBar()
        self.current.setFormat("%v / %m")
        layout.addWidget(QLabel("Overall"))
        layout.addWidget(self.overall)
        layout.addWidget(QLabel("Current operation"))
        layout.addWidget(self.current)
        summary = QGroupBox("CAMDS Import Progress")
        form = QFormLayout(summary)
        self.labels = {name: QLabel("-") for name in ("Stage", "Completed", "Components", "Materials", "Substances", "Warnings", "Errors", "Elapsed", "ETA", "Rate", "Current item", "CAMDS action")}
        for name, label in self.labels.items():
            form.addRow(f"{name}:", label)
        layout.addWidget(summary)
        layout.addStretch()

    def set_overall(self, percent: int) -> None:
        if self.overall.maximum() == 0:
            self.overall.setRange(0, 100)
        self.overall.setValue(percent)

    def begin_busy(self) -> None:
        self.overall.setRange(0, 0)
        self.labels["Stage"].setText("Loading PDF…")

    def set_operation(self, current: int, total: int) -> None:
        self.current.setMaximum(max(total, 1))
        self.current.setValue(current)
        self.labels["Completed"].setText(f"Reading PDF page {current} / {total}")

    def set_stage(self, stage: str) -> None:
        self.labels["Stage"].setText(stage.replace("_", " ").title())

    def update_progress(self, progress: ImportProgress) -> None:
        self.labels["Completed"].setText(f"{progress.completed_nodes} / {progress.total_nodes}")
        self.labels["Components"].setText(f"{progress.completed_components} / {progress.total_components}")
        self.labels["Materials"].setText(f"{progress.completed_materials} / {progress.total_materials}")
        self.labels["Substances"].setText(f"{progress.completed_substances} / {progress.total_substances}")
        self.labels["Warnings"].setText(str(progress.warnings))
        self.labels["Errors"].setText(str(progress.errors))
        self.labels["Elapsed"].setText(f"{progress.elapsed_seconds:.1f} s")
        self.labels["Current item"].setText(progress.current_node_name or "-")
