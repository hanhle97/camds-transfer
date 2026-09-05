"""One browser and one asyncio loop per session; commands cross threads by queue."""
from __future__ import annotations

import asyncio
import queue
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from playwright.async_api import async_playwright

from ..camds.operations import CamdsOperations, SEARCH_URL


class OperationsWorker(QThread):
    ready = Signal()
    result = Signal(object)
    failed = Signal(str, bool)
    session_error = Signal(str)
    operation_progress = Signal(str)

    def __init__(self, storage: Path, parent=None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.commands: queue.Queue = queue.Queue()
        self.stopping = threading.Event()

    def submit(self, action: str, request) -> None:
        self.commands.put((action, request))

    def stop(self) -> None:
        self.stopping.set()

    def run(self) -> None:
        try:
            asyncio.run(self._session())
        except Exception as exc:
            self.session_error.emit(str(exc))

    async def _session(self) -> None:
        async with async_playwright() as runtime:
            browser = await runtime.chromium.launch(headless=False)
            task = None
            try:
                options = {"storage_state": self.storage} if self.storage.is_file() else {}
                context = await browser.new_context(**options)
                page = await context.new_page()
                page.set_default_timeout(15_000)
                operations = CamdsOperations(page)
                # The user can sign in manually in this window if saved login expired.
                startup = asyncio.create_task(page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=60_000))
                task = startup
                while not startup.done() and not self.stopping.is_set():
                    await asyncio.sleep(0.1)
                if self.stopping.is_set():
                    return
                await startup
                task = None
                self.ready.emit()
                while not self.stopping.is_set() and not page.is_closed() and browser.is_connected():
                    if task is not None and task.done():
                        try:
                            self.result.emit(task.result())
                        except Exception as exc:
                            self.failed.emit(str(exc), operations.editor_open)
                        task = None
                    if task is None:
                        try:
                            action, request = self.commands.get_nowait()
                        except queue.Empty:
                            pass
                        else:
                            if action not in ("search", "create", "save"):
                                self.failed.emit("Unsupported CAMDS operation", operations.editor_open)
                            else:
                                self.operation_progress.emit("CAMDS: " + ("Searching…" if action == "search" else "Creating MDS root…"))
                                task = asyncio.create_task(getattr(operations, action)(request))
                    await asyncio.sleep(0.1)
            finally:
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await browser.close()
