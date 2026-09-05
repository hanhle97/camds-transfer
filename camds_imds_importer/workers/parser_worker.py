from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot
import pymupdf

from ..core.statistics import calculate_statistics
from ..parser.models import MDSDocument
from ..parser.pdf_parser import parse_pdf


class ParserWorker(QObject):
    progress_changed = Signal(int)
    operation_progress_changed = Signal(int, int)
    stage_changed = Signal(str)
    log_message = Signal(str)
    error = Signal(str)
    completed = Signal(object, object)
    finished = Signal()

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    @Slot()
    def run(self) -> None:
        try:
            self.stage_changed.emit("PDF_LOADING")
            self.log_message.emit(f"Loading PDF: {self.path.name}")
            # PyMuPDF reads the document catalog quickly; do not block the UI
            # on full pypdf page-tree expansion before publishing progress.
            with pymupdf.open(self.path) as pdf:
                total_pages = pdf.page_count
            self.operation_progress_changed.emit(0, total_pages)
            self.progress_changed.emit(1)
            self.stage_changed.emit("PDF_PARSING")

            def on_page(current: int, total: int) -> None:
                self.operation_progress_changed.emit(current, total)
                self.progress_changed.emit(min(24, 1 + int(23 * current / max(total, 1))))

            document: MDSDocument = parse_pdf(self.path, on_page)
            self.stage_changed.emit("TREE_BUILDING")
            statistics = calculate_statistics(document)
            self.progress_changed.emit(25)
            self.log_message.emit(f"Tree contains {statistics.total_nodes} nodes")
            self.completed.emit(document, statistics)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()
