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

from ..camds.api import BASE_URL, CamdsApi, CamdsApiError
from ..camds.browser_runtime import chromium_present, install_chromium
from ..camds.api_backend import ApiBackend
from ..camds.action_policy import CamdsAction, SensitiveActionBlocked, require_action_confirmation
from ..camds.discovery import discover_material_classifications
from ..camds.import_control import ImportControl
from ..camds.import_plan import MAX_REPORTED_ERRORS
from ..camds.page_transport import PageTransport
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
        self._loading: asyncio.Task | None = None
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
    async def _open_browser(self, runtime):
        """Launch Chromium, and open the page every CAMDS API call runs in.

        `context.request` was not enough. It shares the cookie jar, but it is
        still a Node HTTP client that resolves DNS and picks a proxy itself, and
        on some networks it could not reach CAMDS at all -

            getaddrinfo ENOTFOUND catarc.camds.org.cn

        - while the signed-in window beside it worked. A `fetch()` evaluated in
        a page cannot diverge like that: it is the browser's own stack, so
        whatever DNS, proxy and TLS trust let the operator sign in serve the API
        too, and the session cookie goes with it because it is same-origin.

        This page is never shown. The visible window is the operator's, and
        closing it must not stop an import.
        """
        await asyncio.to_thread(self._ensure_chromium)
        browser = await runtime.chromium.launch(headless=False)
        options = {"storage_state": str(self.storage)} if self.storage.is_file() else {}
        context = await browser.new_context(**options)
        api_page = await context.new_page()
        # Only the origin matters for a same-origin fetch, so this does not wait
        # for the single-page application to finish drawing.
        await api_page.goto(BASE_URL, wait_until="commit", timeout=NAVIGATION_TIMEOUT_MS)
        return browser, context, PageTransport(api_page)

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

    def _ensure_chromium(self) -> None:
        """Fetch the browser once if this machine has never had it.

        A built executable carries Playwright's driver but not its 430 MB
        browser. Without this the first window fails telling the operator to
        run `playwright install`, which is not a command they have.
        """
        if chromium_present():
            return
        self.notice.emit(
            "Downloading the browser CAMDS is driven through (about 430 MB). "
            "This happens once on this machine.")
        install_chromium(on_output=lambda line: self.operation_progress.emit("CAMDS: " + line))
        self.notice.emit("Browser installed.")

    async def _open_page(self, context):
        """Put a window on the context. The context outlives it.

        Closing a page does not close its context in Playwright, so the session
        survives the operator closing the window - which is the whole point of
        keeping the two apart.

        The page is handed back as soon as it exists. CAMDS is a large
        single-page application on a slow link, and waiting for it to finish
        drawing used to hold every control disabled for up to three minutes
        behind "Opening CAMDS browser…", with the window already on screen.
        """
        page = await context.new_page()
        page.set_default_timeout(15_000)
        self.browser_changed.emit(True)

        async def load() -> None:
            try:
                await self._open_start_page(page)
            except Exception as exc:
                self.notice.emit(
                    "CAMDS is taking a long time to load (" + str(exc).splitlines()[0] + "). "
                    "The window is open: let the page finish, or sign in there, and carry on.")

        # Kept so it is cancelled with the session rather than outliving it.
        self._loading = asyncio.create_task(load())
        return page, CamdsOperations(page)

    async def _close_page(self, page) -> None:
        try:
            await page.close()
        except Exception:
            pass
        self.browser_changed.emit(False)

    async def _session(self) -> None:
        async with async_playwright() as runtime:
            browser = context = page = operations = api = None
            task = None
            authenticated = False
            try:
                browser, context, api = await self._open_browser(runtime)
                page, operations = await self._open_page(context)
                self.ready.emit()
                next_poll = 0.0
                while not self.stopping.is_set():
                    now = asyncio.get_running_loop().time()
                    if page is not None and page.is_closed():
                        # The window is the operator's to close. The session is
                        # not inside it: it is in the browser context, which
                        # stays, cookies and proxy and all.
                        page = operations = None
                        self.browser_changed.emit(False)
                        self.notice.emit(
                            "CAMDS browser window closed. The signed-in session is kept - "
                            "Open CAMDS browser starts another window on it.")
                    if not browser.is_connected():
                        # Chromium itself is gone, so the session went with it.
                        # Rebuild from the last saved state rather than stop.
                        self.notice.emit("The CAMDS browser exited; reopening it on the saved session.")
                        browser, context, api = await self._open_browser(runtime)
                        page = operations = None
                    editor_open = operations.editor_open if operations else False
                    if task is not None and task.done():
                        try:
                            self.result.emit(task.result())
                        except Exception as exc:
                            self.failed.emit(str(exc), editor_open)
                        task = None
                        next_poll = 0.0
                    if task is None and now >= next_poll and not editor_open:
                        # One authenticated context, so what the window shows and
                        # what the API sees can no longer disagree.
                        status = await self._api_status(api, authenticated)
                        authenticated = await self._note_session(context, status, authenticated)
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
                                if (needs_page or action == "open_browser") and page is None:
                                    page, operations = await self._open_page(context)
                                if action == "open_browser":
                                    self.result.emit({
                                        "kind": "open_browser", "identity": "", "editor_open": False,
                                        "note": "CAMDS window open on the current session."})
                                elif action == "close_browser":
                                    if page is not None:
                                        if authenticated:
                                            await self._remember_session(context)
                                        await self._close_page(page)
                                        page = operations = None
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
                                    task = asyncio.create_task(importer.run(
                                        request, resume=options.get("resume", False),
                                        reuse=options.get("reuse", True)))
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
                for pending in (task, self._loading):
                    if pending is not None and not pending.done():
                        pending.cancel()
                        await asyncio.gather(pending, return_exceptions=True)
                if authenticated and context is not None:
                    # CAMDS can hand out a fresh cookie during a session, so the
                    # last state is worth more than the one saved at sign-in.
                    await self._remember_session(context)
                if browser is not None:
                    try:
                        await browser.close()
                    except Exception:
                        pass
                    self.browser_changed.emit(False)
