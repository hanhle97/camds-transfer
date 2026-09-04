from __future__ import annotations

import asyncio
import threading

from PySide6.QtCore import QObject, Signal, Slot

from ..camds.browser import CamdsBrowser
from ..camds.login import LoginStatus
from ..core.credentials import Credentials


class CamdsLoginWorker(QObject):
    stage_changed = Signal(str)
    completed = Signal(object)
    error = Signal(str)
    verification_screenshot = Signal(bytes)
    finished = Signal()

    def __init__(self, browser: CamdsBrowser, credentials: Credentials) -> None:
        super().__init__()
        self.browser = browser
        self.credentials = credentials
        self._verification_code: str | None = None
        self._code_lock = threading.Lock()

    @Slot()
    def run(self) -> None:
        try:
            self.stage_changed.emit("CAMDS_LOGIN")
            result = asyncio.run(self.browser.test_login(
                self.credentials,
                self._on_status,
                screenshot_callback=self._on_screenshot,
                verification_code_provider=self._get_code,
            ))
            self.completed.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()

    def _on_status(self, status: LoginStatus) -> None:
        if status == LoginStatus.WAITING_USER_VERIFICATION:
            self.stage_changed.emit("CAMDS_WAITING_VERIFICATION")

    def _on_screenshot(self, image: bytes) -> None:
        self.verification_screenshot.emit(image)

    def _get_code(self) -> str | None:
        with self._code_lock:
            code, self._verification_code = self._verification_code, None
            return code

    def set_verification_code(self, code: str) -> None:
        with self._code_lock:
            self._verification_code = code.strip() or None
