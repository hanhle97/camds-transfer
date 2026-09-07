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

from ..camds.api import CamdsApi, CamdsApiError
from ..camds.api_backend import ApiBackend
from ..camds.action_policy import CamdsAction, SensitiveActionBlocked, require_action_confirmation
from ..camds.discovery import discover_material_classifications
from ..camds.import_control import ImportControl
from ..camds.import_plan import MAX_REPORTED_ERRORS
from ..camds.login import LoginStatus
from ..camds.operations import CamdsOperations, SEARCH_URL
from ..camds.session import SessionStatus, session_status, sign_in
from ..camds.tree_import import TreeImporter, DraftBrowser

LOGIN_URL = "https://catarc.camds.org.cn/#/login"
PROGRESS_TEXT = {
    "search": "Searching…",
    "create": "Creating MDS root…",
    "save": "Saving…",
    "leave_editor": "Leaving the editor…",
    "discover_classifications": "Recording the classification wizard…",
    "check_substances": "Checking every substance in the catalogue…",
    "import_tree": "Importing parsed tree…",
    "login": "Signing in…",
    "open_browser": "Opening a CAMDS browser window…",
    "close_browser": "Closing the browser window…",
}

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
    "check_substances": CamdsAction.SEARCH,
    "import_tree": CamdsAction.SAVE_DRAFT,
    "open_browser": CamdsAction.OPEN,
    "close_browser": CamdsAction.OPEN,
}

# Actions that need a rendered page. Everything else runs on the API context,
# which outlives any window.
NEEDS_BROWSER = frozenset({"search", "create", "save", "leave_editor",
                           "discover_classifications", "login"})


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
    # True when a CAMDS window is open. The session does not depend on it.
    browser_changed = Signal(bool)

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

    async def _check_substances(self, api, request):
        """Look up every distinct substance this import needs. Read-only.

        An unresolved substance stops a run - a Material missing part of itself
        is wrong data, not incomplete data - and the real tree spends hours
        before it reaches most of them. The same lookup the import performs is
        run here first, so the whole list is known in minutes with nothing
        created.
        """
        # The first search is also the session test the old "Test API session"
        # button performed: an expired session answers the login page, not JSON.
        backend = ApiBackend(CamdsApi(api))
        request = request.snapshot()
        nodes = request.substance_lookups()
        unresolved = []
        for index, node in enumerate(nodes, start=1):
            if self.stopping.is_set():
                break
            if index % 20 == 0 or index == len(nodes):
                self.operation_progress.emit(
                    f"CAMDS: checking substance {index}/{len(nodes)}…")
            try:
                await backend.resolve_substance(node)
            except CamdsApiError as exc:
                unresolved.append(str(exc))
        # Write the questions next to the rows CAMDS offered, so a choice is
        # made against the evidence rather than from a log line.
        for node, rows in backend.pending:
            backend.substances.ask(node, rows)
        picked = await backend.read_back_findings()
        note = (f"Checked {len(nodes)} distinct substance lookup(s) for "
                f"{len(request.materials())} Material(s). Nothing was created. ")
        if not unresolved and not picked:
            note += "Every one resolved to exactly one CAMDS entry."
        else:
            if picked:
                note += (f"{len(picked)} did not identify one substance and the first row "
                         f"CAMDS offered was taken; review them in {backend.substances.path} "
                         "(\"source\": \"first-row\"). ")
            if unresolved:
                note += (f"{len(unresolved)} could not be resolved at all and would stop an "
                         f"import; they are written to {backend.substances.path} with a null "
                         "\"csid\".")
        return {"kind": "check_substances", "identity": "", "editor_open": False,
                "note": note, "warnings": (picked + unresolved)[:MAX_REPORTED_ERRORS],
                "skipped": []}

    async def _note_session(self, context, status, authenticated: bool) -> bool:
        """Report a change in the live session, and keep one that just started.

        However the session was established - and the CAPTCHA means it is
        usually typed into this window by hand, with no sign-in step to hook -
        saving it the moment the page reads as authenticated is what lets the
        next window start from it instead of asking again.
        """
        if status == SessionStatus.UNKNOWN:
            return authenticated
        became = status == SessionStatus.AUTHENTICATED
        if became != authenticated or status == SessionStatus.EXPIRED:
            if became:
                await self._remember_session(context)
            self.session_changed.emit(status.value)
        return became

    async def _remember_session(self, context) -> None:
        """Write the signed-in cookies and localStorage where the next run reads them.

        Only ever called once the live page says it is authenticated, so a
        failed or abandoned sign-in never overwrites a working session.
        """
        try:
            self.storage.parent.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=self.storage)
        except Exception as exc:
            # Losing the saved session costs one more sign-in, not the run.
            self.notice.emit("Could not save the CAMDS session for reuse "
                             f"({str(exc).splitlines()[0]}); you may have to sign in again next time.")

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

    # --------------------------------------------------------------- session
    async def _api_context(self, runtime):
        """A request context of its own, holding the saved cookies.

        It is not taken from a browser context, so closing the window does not
        take the session with it: the import, the catalogue check and the status
        poll all keep working with no window open at all.
        """
        options = {"storage_state": str(self.storage)} if self.storage.is_file() else {}
        return await runtime.request.new_context(**options)

    async def _adopt_browser_session(self, runtime, api, browser_context):
        """Carry a sign-in made in the window over to the request context.

        A request context holds the cookies it was built with, so one built
        before a login stays anonymous however well the window is doing. After a
        sign-in the state is saved and the context rebuilt from it.
        """
        await self._remember_session(browser_context)
        try:
            await api.dispose()
        except Exception:
            pass
        return await self._api_context(runtime)

    async def _api_status(self, api, was_authenticated: bool):
        """Ask CAMDS itself, with one read-only call the import depends on."""
        try:
            await CamdsApi(api).find_material(name="__camds_api_session_probe__")
        except CamdsApiError:
            # An expired session answers with the login page rather than JSON.
            return SessionStatus.EXPIRED if was_authenticated else SessionStatus.LOGIN_REQUIRED
        except Exception:
            # Unreachable for a moment is not evidence that the session ended.
            return SessionStatus.UNKNOWN
        return SessionStatus.AUTHENTICATED

    async def _open_window(self, runtime):
        """Launch a window on the saved session; its cookies are the same ones."""
        browser = await runtime.chromium.launch(headless=False)
        options = {"storage_state": str(self.storage)} if self.storage.is_file() else {}
        context = await browser.new_context(**options)
        page = await context.new_page()
        page.set_default_timeout(15_000)
        try:
            await self._open_start_page(page)
        except Exception as exc:
            self.notice.emit(
                "CAMDS did not finish loading in time (" + str(exc).splitlines()[0] + "). "
                "The window is open: let the page finish, then run the operation again.")
        self.browser_changed.emit(True)
        return browser, context, page, CamdsOperations(page)

    async def _close_window(self, browser) -> None:
        try:
            await browser.close()
        except Exception:
            pass
        self.browser_changed.emit(False)

    async def _session(self) -> None:
        async with async_playwright() as runtime:
            api = await self._api_context(runtime)
            browser = context = page = operations = None
            task = None
            authenticated = False
            try:
                browser, context, page, operations = await self._open_window(runtime)
                self.ready.emit()
                next_poll = 0.0
                while not self.stopping.is_set():
                    now = asyncio.get_running_loop().time()
                    if browser is not None and (not browser.is_connected() or page.is_closed()):
                        # The window is the operator's to close. The session is
                        # not inside it: it is in the request context and the file.
                        browser = context = page = operations = None
                        self.browser_changed.emit(False)
                        self.notice.emit(
                            "CAMDS browser window closed. The signed-in session is kept - "
                            "Open CAMDS browser starts another window on it.")
                    editor_open = operations.editor_open if operations else False
                    if task is not None and task.done():
                        try:
                            self.result.emit(task.result())
                        except Exception as exc:
                            self.failed.emit(str(exc), editor_open)
                        task = None
                        next_poll = 0.0
                    if task is None and now >= next_poll and not editor_open:
                        status = await self._api_status(api, authenticated)
                        if status != SessionStatus.AUTHENTICATED and page is not None:
                            # A sign-in just made in the window has not reached
                            # the request context yet.
                            if await session_status(page) == SessionStatus.AUTHENTICATED:
                                api = await self._adopt_browser_session(runtime, api, context)
                                status = await self._api_status(api, authenticated)
                        authenticated = await self._note_session(api, status, authenticated)
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
                                self.failed.emit(str(exc), editor_open)
                            else:
                                self.operation_progress.emit("CAMDS: " + PROGRESS_TEXT[action])
                                needs_page = action in NEEDS_BROWSER or (
                                    action == "import_tree" and not self.use_api)
                                if (needs_page or action == "open_browser") and browser is None:
                                    browser, context, page, operations = await self._open_window(runtime)
                                if action == "open_browser":
                                    self.result.emit({
                                        "kind": "open_browser", "identity": "", "editor_open": False,
                                        "note": "CAMDS window open on the current session."})
                                elif action == "close_browser":
                                    if browser is not None:
                                        if authenticated:
                                            await self._remember_session(context)
                                        await self._close_window(browser)
                                        browser = context = page = operations = None
                                    self.result.emit({
                                        "kind": "close_browser", "identity": "", "editor_open": False,
                                        "note": "Window closed. The CAMDS session is kept."})
                                elif action == "import_tree":
                                    # Reset in place: the UI already holds this object.
                                    self.control.reset()
                                    # A retry during a run of hours must be
                                    # visible, not silently absorbed.
                                    backend = (ApiBackend(CamdsApi(
                                        api,
                                        on_retry=lambda text: self.operation_progress.emit(
                                            "CAMDS: " + text)))
                                        if self.use_api else DraftBrowser(operations))
                                    importer = TreeImporter(backend, progress=self.node_progress.emit,
                                                            control=self.control)
                                    task = asyncio.create_task(importer.run(request, resume=options.get("resume", False)))
                                elif action == "check_substances":
                                    task = asyncio.create_task(self._check_substances(api, request))
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
                if authenticated:
                    # CAMDS can hand out a fresh cookie during a session, so the
                    # last state is worth more than the one saved at sign-in.
                    await self._remember_session(context if context is not None else api)
                if browser is not None:
                    await self._close_window(browser)
                try:
                    await api.dispose()
                except Exception:
                    pass
