"""The session is not inside the browser window.

Closing the window used to end the whole worker: the app reported "not
connected" and the operator had to sign in again, although CAMDS had not
logged anybody out. The session lives in a request context of its own, built
from the saved cookies, so the window is a view on it and nothing more.
"""
import asyncio
import json
from pathlib import Path

import pytest

from camds_imds_importer.camds.session import SessionStatus
from camds_imds_importer.workers.operations_worker import (
    ACTION_POLICY, NEEDS_BROWSER, OperationsWorker)


def _state(path: Path) -> Path:
    path.write_text(json.dumps({
        "cookies": [{"name": "SESSION", "value": "x", "domain": "catarc.camds.org.cn",
                     "path": "/", "expires": -1}],
        "origins": []}), encoding="utf-8")
    return path


class Api:
    """Answers the session probe the way CAMDS does."""

    def __init__(self, authenticated=True):
        self.authenticated = authenticated
        self.disposed = False

    async def post(self, url, params=None, data=None, headers=None, timeout=None):
        body = ({"respCode": "0", "ok": True, "data": {"records": []}} if self.authenticated
                else {"respCode": "1", "ok": False, "message": "not signed in"})

        class Response:
            status = 200

            @staticmethod
            async def json():
                return body
        return Response

    async def dispose(self):
        self.disposed = True

    async def storage_state(self, path):
        _state(Path(path))


async def test_a_signed_in_session_is_read_from_the_request_context(tmp_path):
    worker = OperationsWorker(_state(tmp_path / "state.json"))
    assert await worker._api_status(Api(), False) is SessionStatus.AUTHENTICATED


async def test_an_expired_session_is_told_apart_from_one_never_established(tmp_path):
    """Both mean "sign in", but only one of them means something changed."""
    worker = OperationsWorker(_state(tmp_path / "state.json"))
    assert await worker._api_status(Api(False), True) is SessionStatus.EXPIRED
    assert await worker._api_status(Api(False), False) is SessionStatus.LOGIN_REQUIRED


async def test_being_unreachable_is_not_evidence_the_session_ended(tmp_path):
    class Down:
        async def post(self, url, params=None, data=None, headers=None, timeout=None):
            raise asyncio.TimeoutError("no route")

    worker = OperationsWorker(_state(tmp_path / "state.json"))
    assert await worker._api_status(Down(), True) is SessionStatus.UNKNOWN


async def test_a_sign_in_made_in_the_window_reaches_the_request_context(tmp_path):
    """A request context holds the cookies it was built with, so one built
    before a login stays anonymous until it is rebuilt from the saved state."""
    storage = tmp_path / "state.json"
    worker = OperationsWorker(storage)
    stale, built = Api(authenticated=False), []

    class Runtime:
        class request:
            @staticmethod
            async def new_context(**options):
                built.append(options)
                return Api(authenticated=True)

    fresh = await worker._adopt_browser_session(Runtime(), stale, Api())
    assert stale.disposed, "the anonymous context must not be left behind"
    assert storage.is_file(), "the window's cookies are saved first"
    assert built and built[0]["storage_state"] == str(storage)
    assert await worker._api_status(fresh, False) is SessionStatus.AUTHENTICATED


def test_only_the_actions_that_draw_a_page_need_a_window():
    """Everything else runs on the request context, with no window at all."""
    assert NEEDS_BROWSER == {"search", "create", "save", "leave_editor",
                             "discover_classifications", "login"}
    for action in ("import_tree", "check_substances"):
        assert action in ACTION_POLICY
        assert action not in NEEDS_BROWSER, f"{action} must survive a closed window"


def test_opening_and_closing_a_window_are_ordinary_actions():
    """Closing one is not stopping the session, so it goes through the queue
    and is classified like everything else."""
    from camds_imds_importer.camds.action_policy import CamdsAction

    assert ACTION_POLICY["open_browser"] is CamdsAction.OPEN
    assert ACTION_POLICY["close_browser"] is CamdsAction.OPEN


def test_the_tab_asks_for_a_window_instead_of_a_second_session(tmp_path):
    """Open CAMDS browser on a running session must not start another worker."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from camds_imds_importer.ui.camds_tab import CamdsTab

    QApplication.instance() or QApplication([])
    tab = CamdsTab()
    sent = []
    tab.worker = type("W", (), {
        "stopping": type("E", (), {"is_set": staticmethod(lambda: False)})(),
        "submit": lambda _self, action, request, **options: sent.append(action),
    })()
    existing = tab.worker
    tab.open_session()
    assert sent == ["open_browser"]
    assert tab.worker is existing, "a second session would be a second login"
    assert tab.busy, "one CAMDS operation at a time"
    tab.busy = False   # as if the window had finished opening
    tab.close_browser()
    assert sent == ["open_browser", "close_browser"]
    tab.worker = None


async def test_the_window_is_usable_before_camds_finishes_drawing(tmp_path):
    """CAMDS is a large single-page application on a slow link. Waiting for it
    held every control disabled behind "Opening CAMDS browser…" for up to three
    minutes, with the window already on screen."""
    import asyncio

    worker = OperationsWorker(_state(tmp_path / "state.json"))
    still_loading = asyncio.Event()
    opened = []
    worker.browser_changed = type("S", (), {"emit": lambda _s, v: opened.append(v)})()
    worker.notice = type("S", (), {"emit": lambda _s, text: None})()

    class Page:
        def set_default_timeout(self, _ms): pass
        async def goto(self, *a, **k): await still_loading.wait()
        async def wait_for_function(self, *a, **k): await still_loading.wait()

    class Context:
        async def new_page(self): return Page()

    class Browser:
        async def new_context(self, **options): return Context()

    class Runtime:
        class chromium:
            @staticmethod
            async def launch(**k): return Browser()

    worker._ensure_chromium = lambda: None
    browser, context, page, operations = await asyncio.wait_for(
        worker._open_window(Runtime()), timeout=2)

    assert browser is not None and page is not None
    assert opened == [True], "the window is reported the moment it exists"
    assert worker._loading is not None and not worker._loading.done()
    still_loading.set()
    await asyncio.wait_for(worker._loading, timeout=2)
