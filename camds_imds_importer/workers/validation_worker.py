from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from ..parser.models import MDSNode
from ..validation.validator import validate_document


class ValidationWorker(QObject):
    progress_changed = Signal(int)
    stage_changed = Signal(str)
    completed = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, root: MDSNode) -> None:
        super().__init__()
        self.root = root

    @Slot()
    def run(self) -> None:
        try:
            self.stage_changed.emit("VALIDATING")
            issues = validate_document(self.root)
            self.progress_changed.emit(30)
            self.completed.emit(issues)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()
