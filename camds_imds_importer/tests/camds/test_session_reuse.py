"""Reusing a CAMDS sign-in instead of asking for it again.

CAMDS carries the session in a cookie (`SESSION`, `catarc_infoSysSid`) plus
localStorage, and Playwright's storage_state holds exactly those. The state was
only ever written by the scripted sign-in - but the CAPTCHA means the operator
usually types the login into the headed window by hand, and that path saved
nothing, so every launch asked again while a day-old file sat on disk.

It is written whenever the live page is seen authenticated. The file is never
evidence on its own: session_status still reads the page every poll.
"""
import json
from pathlib import Path

import pytest

from camds_imds_importer.camds.session import SessionStatus
from camds_imds_importer.workers.operations_worker import OperationsWorker


class Context:
    def __init__(self, fail=None):
        self.fail = fail

    async def storage_state(self, path):
        if self.fail:
            raise RuntimeError(self.fail)
        Path(path).write_text(json.dumps(
            {"cookies": [{"name": "SESSION", "domain": "catarc.camds.org.cn", "expires": -1}],
             "origins": [{"origin": "https://catarc.camds.org.cn",
                          "localStorage": [{"name": "vuex", "value": "{}"}]}]}), encoding="utf-8")


def _worker(tmp_path):
    """A worker whose Qt signals are collected instead of emitted."""
    said = []
    worker = OperationsWorker(tmp_path / "camds_storage_state.json")
    worker.session_changed = type("S", (), {"emit": lambda _s, text: said.append(text)})()
    worker.notice = type("S", (), {"emit": lambda _s, text: said.append(text)})()
    return worker, said


async def test_a_signed_in_session_is_written_where_the_next_run_reads_it(tmp_path):
    storage = tmp_path / "nested" / "camds_storage_state.json"
    worker = OperationsWorker(storage)
    await worker._remember_session(Context())
    saved = json.loads(storage.read_text(encoding="utf-8"))
    assert [c["name"] for c in saved["cookies"]] == ["SESSION"]
    assert storage.parent.is_dir(), "the directory is created rather than assumed"


async def test_failing_to_save_the_session_is_reported_not_raised(tmp_path):
    """Losing the saved session costs one more sign-in, never the run."""
    storage = tmp_path / "camds_storage_state.json"
    worker = OperationsWorker(storage)
    said = []
    worker.notice = type("S", (), {"emit": lambda _self, text: said.append(text)})()
    await worker._remember_session(Context(fail="disk is full"))
    assert said and "sign in again" in said[0]
    assert not storage.exists()


async def test_the_session_is_saved_the_moment_the_page_reads_as_signed_in(tmp_path):
    """Not at sign-in: a manual login in the headed window has no sign-in step."""
    worker, said = _worker(tmp_path)
    context = Context()
    assert await worker._note_session(context, SessionStatus.AUTHENTICATED, False) is True
    assert (tmp_path / "camds_storage_state.json").is_file()
    assert said == ["AUTHENTICATED"]


async def test_a_session_that_was_already_known_is_not_written_again(tmp_path):
    worker, said = _worker(tmp_path)
    assert await worker._note_session(Context(), SessionStatus.AUTHENTICATED, True) is True
    assert not (tmp_path / "camds_storage_state.json").exists()
    assert said == []


async def test_an_expired_session_never_overwrites_the_saved_one(tmp_path):
    """Otherwise one lapse would throw away the state and force a fresh login."""
    worker, said = _worker(tmp_path)
    assert await worker._note_session(Context(), SessionStatus.EXPIRED, True) is False
    assert await worker._note_session(Context(), SessionStatus.LOGIN_REQUIRED, False) is False
    assert not (tmp_path / "camds_storage_state.json").exists()
    assert said == ["EXPIRED"]


async def test_a_page_that_cannot_be_read_changes_nothing(tmp_path):
    """A navigation in flight is not evidence that the session ended."""
    worker, said = _worker(tmp_path)
    assert await worker._note_session(Context(), SessionStatus.UNKNOWN, True) is True
    assert said == []
