"""One session for login and operations, with expiry detected from the live page."""
import pytest

from camds_imds_importer.camds import session as session_module
from camds_imds_importer.camds.action_policy import CamdsAction, SensitiveActionBlocked, require_action_confirmation
from camds_imds_importer.camds.login import LoginStatus
from camds_imds_importer.camds.session import SessionStatus, session_status, sign_in
from camds_imds_importer.workers.operations_worker import ACTION_POLICY


class FakePage:
    def __init__(self, storage_sink=None):
        self.url = "https://catarc.camds.org.cn/#/seek/seekMaterialDataSheet"
        self.context = self
        self.storage_calls = []

    async def storage_state(self, path=None):
        self.storage_calls.append(path)


@pytest.mark.parametrize("authenticated, was, expected", [
    (True, False, SessionStatus.AUTHENTICATED),
    (False, True, SessionStatus.EXPIRED),
    (False, False, SessionStatus.LOGIN_REQUIRED),
])
async def test_expiry_is_distinguished_from_never_signed_in(monkeypatch, authenticated, was, expected):
    monkeypatch.setattr(session_module, "is_authenticated", lambda page: _async(authenticated))
    assert await session_status(FakePage(), was_authenticated=was) == expected


async def test_navigation_in_flight_is_not_reported_as_a_lost_session(monkeypatch):
    async def boom(page):
        raise RuntimeError("Execution context was destroyed")
    monkeypatch.setattr(session_module, "is_authenticated", boom)
    assert await session_status(FakePage(), was_authenticated=True) == SessionStatus.UNKNOWN


async def test_sign_in_reuses_the_open_session_instead_of_logging_in_again(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(session_module, "is_authenticated", lambda page: _async(True))
    monkeypatch.setattr(session_module, "login", lambda *a, **k: called.append(a))
    page = FakePage()
    result = await sign_in(page, None, login_url="x", storage_state_path=tmp_path / "state.json")
    assert result.status == LoginStatus.AUTHENTICATED
    assert not called, "an already authenticated page must not be re-submitted to the login form"
    assert page.storage_calls == [tmp_path / "state.json"]


def test_every_action_has_the_words_shown_while_it_runs():
    """An action added to the policy but not to a message map reached the
    operator as a KeyError, after the click, with the operation lost."""
    from camds_imds_importer.ui.camds_tab import SUBMIT_STATUS
    from camds_imds_importer.workers.operations_worker import PROGRESS_TEXT

    assert set(PROGRESS_TEXT) == set(ACTION_POLICY), "worker progress text"
    assert set(SUBMIT_STATUS) == set(ACTION_POLICY), "status shown on submit"


def test_every_dispatchable_action_is_classified_and_sensitive_ones_fail_closed():
    assert set(ACTION_POLICY) == {"login", "search", "discover_classifications",
                                  "import_tree", "check_substances",
                                  "open_browser", "close_browser"}
    for action in ACTION_POLICY.values():
        require_action_confirmation(action)  # automatable actions need no confirmation
    for sensitive in (CamdsAction.DELETE, CamdsAction.SEND, CamdsAction.PROPOSE, CamdsAction.SUBMIT):
        assert sensitive not in ACTION_POLICY.values()
        with pytest.raises(SensitiveActionBlocked):
            require_action_confirmation(sensitive)


async def _async(value):
    return value
