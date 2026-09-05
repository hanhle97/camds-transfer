from __future__ import annotations

import asyncio
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..camds.browser import CamdsBrowser


class DiscoveryWorker(QObject):
    completed = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, browser: CamdsBrowser, target: Path, base_url: str) -> None:
        super().__init__()
        self.browser, self.target, self.base_url = browser, target, base_url

    @Slot()
    def run(self) -> None:
        try:
            self.completed.emit(asyncio.run(self.browser.discover_authenticated(self.target, self.base_url)))
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()
