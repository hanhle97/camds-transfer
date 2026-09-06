"""Authentication state for the one browser session shared by every operation.

Login, Search, Create and tree import all run on the same Playwright page, so
the app can never report "Logged in" while the operations browser is anonymous.
Session validity is re-checked from the live page rather than assumed from a
past login result.
"""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from .login import LoginResult, LoginStatus, is_authenticated, login


class SessionStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    AUTHENTICATED = "AUTHENTICATED"
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    EXPIRED = "EXPIRED"


def storage_state_if_available(path: Path) -> Path | None:
    return path if path.is_file() and path.stat().st_size > 0 else None


async def session_status(page, *, was_authenticated: bool = False) -> SessionStatus:
    """Classify the live page. A session that was valid and is not any more is EXPIRED."""
    try:
        if await is_authenticated(page):
            return SessionStatus.AUTHENTICATED
    except Exception:
        # A navigation in flight is not evidence that the session ended.
        return SessionStatus.UNKNOWN
    return SessionStatus.EXPIRED if was_authenticated else SessionStatus.LOGIN_REQUIRED


async def sign_in(
    page,
    credentials,
    *,
    login_url: str,
    storage_state_path: Path,
    stage_callback=None,
    screenshot_callback=None,
    verification_code_provider=None,
) -> LoginResult:
    """Authenticate the operations page itself; no second browser is launched."""
    if await is_authenticated(page):
        await page.context.storage_state(path=storage_state_path)
        return LoginResult(LoginStatus.AUTHENTICATED, page.url, "Existing CAMDS session reused")
    return await login(
        page, credentials.username, credentials.password,
        login_url=login_url,
        storage_state_path=storage_state_path,
        stage_callback=stage_callback,
        screenshot_callback=screenshot_callback,
        verification_code_provider=verification_code_provider,
    )
