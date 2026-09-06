"""One browser and one asyncio loop per session; commands cross threads by queue.

Login, Search, Create and the recursive tree import all run on this single page,
so an authenticated banner in the app always refers to the browser that will
perform the next operation.
"""
from __future__ import annotations

import asyncio
import os
import queue
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from playwright.async_api import async_playwright

from ..camds.api import CamdsApi
from ..camds.api_backend import ApiBackend
from ..camds.action_policy import CamdsAction, SensitiveActionBlocked, require_action_confirmation
from ..camds.discovery import discover_material_classifications
from ..camds.import_control import ImportControl
from ..camds.login import LoginStatus
from ..camds.operations import CamdsOperations, SEARCH_URL
from ..camds.session import SessionStatus, session_status, sign_in
from ..camds.tree_import import TreeImporter, DraftBrowser

LOGIN_URL = "https://catarc.camds.org.cn/#/login"
SESSION_POLL_SECONDS = 20.0
# CAMDS is reachable from outside China but with a high round-trip time, and the
# SPA pulls large vendor bundles. "domcontentloaded" blocks on every synchronous
# script, which can exceed a minute on such a link.
NAVIGATION_TIMEOUT_MS = int(os.getenv("CAMDS_NAVIGATION_TIMEOUT_MS", "180000"))
APP_SHELL = "() => { const app = document.querySelector('#app'); return app && app.children.length > 0; }"

# Every dispatchable command is classified, so adding a destructive one later
# fails closed at the policy instead of silently reaching the browser.
ACTION_POLICY = {
    "login": CamdsAction.OPEN,
    "search": CamdsAction.SEARCH,
    "create": CamdsAction.CREATE,
    "save": CamdsAction.SAVE_DRAFT,
    "leave_editor": CamdsAction.OPEN,
    "discover_classifications": CamdsAction.READ,
    "api_check": CamdsAction.READ,
    "import_tree": CamdsAction.SAVE_DRAFT,
}


class OperationsWorker(QThread):
    ready = Signal()
    result = Signal(object)
    failed = Signal(str, bool)
    session_error = Signal(str)
    operation_progress = Signal(str)
    node_progress = Signal(object)
    login_stage = Signal(str)
    session_changed = Signal(str)
    notice = Signal(str)

    def __init__(self, storage: Path, parent=None) -> None:
        super().__init__(parent)
        self.storage = storage
        self.commands: queue.Queue = queue.Queue()
        self.stopping = threading.Event()
        self.control = ImportControl()
        # The JSON API is faster and has no wizard dialogs; the browser path
        # stays available for anything the recorded API does not cover.
        self.use_api = os.getenv("CAMDS_USE_API", "1") != "0"
        self._verification_code: str | None = None
        self._code_lock = threading.Lock()

    def submit(self, action: str, request, **options) -> None:
        self.commands.put((action, request, options))

    def stop(self) -> None:
        self.stopping.set()
        # Release an import paused between steps so the loop can exit.
        self.control.stop()

    def set_verification_code(self, code: str) -> None:
        with self._code_lock:
            self._verification_code = code.strip() or None

    def _take_verification_code(self) -> str | None:
        with self._code_lock:
            code, self._verification_code = self._verification_code, None
            return code

    def run(self) -> None:
        try:
            asyncio.run(self._session())
        except Exception as exc:
            self.session_error.emit(str(exc))

    async def _open_start_page(self, page) -> None:
        """Reach the Search page tolerantly: commit first, then wait for the SPA shell."""
        await page.goto(SEARCH_URL, wait_until="commit", timeout=NAVIGATION_TIMEOUT_MS)
        await page.wait_for_function(APP_SHELL, timeout=NAVIGATION_TIMEOUT_MS)

    async def _api_check(self, context):
        """Read-only proof that the JSON API works on this session.

        Nothing is created, so this can be run before committing to an import.
        """
        api = CamdsApi(context.request)
        # Two independent read-only calls: a search the import depends on, and
        # the classification list. Either failing names the exact URL.
        found = await api.find_material(name="__camds_api_session_probe__")
        classifications = await api.material_classifications()
        return {"kind": "api_check", "identity": "", "editor_open": False,
                "note": (f"CAMDS API reachable on this session: search answered "
                         f"({len(found)} row(s)) and {len(classifications)} material "
                         "classification(s) were read. Nothing was created.")}

    async def _login(self, page, credentials):
        result = await sign_in(
            page, credentials,
            login_url=LOGIN_URL,
            storage_state_path=self.storage,
            stage_callback=lambda status: self.login_stage.emit(
                "CAMDS_WAITING_VERIFICATION" if status == LoginStatus.WAITING_USER_VERIFICATION else str(status)),
            verification_code_provider=self._take_verification_code,
        )
        if result.status != LoginStatus.AUTHENTICATED:
            raise RuntimeError(result.message or "CAMDS login did not reach an authenticated state")
        await self._open_start_page(page)
        return {"kind": "login", "identity": "", "editor_open": False, "url": result.url,
                "note": "Signed in on this browser session; Search, Create and tree import use it directly."}

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
                startup = asyncio.create_task(self._open_start_page(page))
                task = startup
                while not startup.done() and not self.stopping.is_set():
                    await asyncio.sleep(0.1)
                if self.stopping.is_set():
                    return
                try:
                    await startup
                except Exception as exc:
                    # A slow first load must not throw away a headed browser the
                    # operator can still watch, sign in to, and retry from. Every
                    # operation re-checks page readiness before acting anyway.
                    self.notice.emit(
                        "CAMDS did not finish loading in time (" + str(exc).splitlines()[0] + "). "
                        "The browser is still open: let the page finish, sign in and select English there, "
                        "then run the operation again.")
                task = None
                self.ready.emit()
                authenticated = False
                next_poll = 0.0
                while not self.stopping.is_set() and not page.is_closed() and browser.is_connected():
                    now = asyncio.get_running_loop().time()
                    if task is not None and task.done():
                        try:
                            self.result.emit(task.result())
                        except Exception as exc:
                            self.failed.emit(str(exc), operations.editor_open)
                        task = None
                        next_poll = 0.0
                    if task is None and now >= next_poll and not operations.editor_open:
                        # Read the live page rather than trusting an earlier login.
                        status = await session_status(page, was_authenticated=authenticated)
                        if status != SessionStatus.UNKNOWN:
                            became = status == SessionStatus.AUTHENTICATED
                            if became != authenticated or status == SessionStatus.EXPIRED:
                                authenticated = became
                                self.session_changed.emit(status.value)
                        next_poll = now + SESSION_POLL_SECONDS
                    if task is None:
                        try:
                            action, request, options = self.commands.get_nowait()
                        except queue.Empty:
                            pass
                        else:
                            try:
                                if action not in ACTION_POLICY:
                                    raise RuntimeError("Unsupported CAMDS operation")
                                require_action_confirmation(ACTION_POLICY[action], confirmed=options.get("confirmed", False))
                            except (RuntimeError, SensitiveActionBlocked) as exc:
                                self.failed.emit(str(exc), operations.editor_open)
                            else:
                                self.operation_progress.emit("CAMDS: " + {
                                    "search": "Searching…", "create": "Creating MDS root…", "save": "Saving…",
                                    "leave_editor": "Leaving the editor…",
                                    "discover_classifications": "Recording the classification wizard…",
                                    "api_check": "Checking the CAMDS API session…",
                                    "import_tree": "Importing parsed tree…", "login": "Signing in…"}[action])
                                if action == "import_tree":
                                    # Reset in place: the UI already holds this object.
                                    self.control.reset()
                                    # The API runs on the signed-in context, so it
                                    # inherits the session the operator established.
                                    backend = (ApiBackend(CamdsApi(context.request))
                                               if self.use_api else DraftBrowser(operations))
                                    importer = TreeImporter(backend, progress=self.node_progress.emit,
                                                            control=self.control)
                                    task = asyncio.create_task(importer.run(request, resume=options.get("resume", False)))
                                elif action == "api_check":
                                    task = asyncio.create_task(self._api_check(context))
                                elif action == "discover_classifications":
                                    task = asyncio.create_task(discover_material_classifications(
                                        operations, Path("debug") / "material-classifications"))
                                elif action == "login":
                                    task = asyncio.create_task(self._login(page, request))
                                else:
                                    task = asyncio.create_task(getattr(operations, action)(request))
                    await asyncio.sleep(0.1)
            finally:
                if task is not None and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await browser.close()
