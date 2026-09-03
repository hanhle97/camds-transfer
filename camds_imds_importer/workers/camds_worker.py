from __future__ import annotations

import asyncio

from PySide6.QtCore import QObject, Signal, Slot

from ..camds.browser import CamdsBrowser
from ..camds.login import LoginStatus
from ..core.credentials import Credentials


class CamdsLoginWorker(QObject):
    stage_changed = Signal(str)
    completed = Signal(object)
    error = Signal(str)
    finished = Signal()

    def __init__(self, browser: CamdsBrowser, credentials: Credentials) -> None:
        super().__init__()
        self.browser = browser
        self.credentials = credentials

    @Slot()
    def run(self) -> None:
        try:
            self.stage_changed.emit("CAMDS_LOGIN")
            result = asyncio.run(self.browser.test_login(self.credentials, self._on_status))
            self.completed.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()

    def _on_status(self, status: LoginStatus) -> None:
        if status == LoginStatus.WAITING_USER_VERIFICATION:
            self.stage_changed.emit("CAMDS_WAITING_VERIFICATION")
