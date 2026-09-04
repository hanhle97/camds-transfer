from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from playwright.async_api import Locator, Page

from .selectors import AUTHENTICATED, LOGIN_BUTTON, LOGIN_PASSWORD, LOGIN_USERNAME, VERIFICATION, VERIFICATION_INPUT, VERIFICATION_SUBMIT, SelectorStrategy


class LoginStatus(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    WAITING_USER_VERIFICATION = "WAITING_USER_VERIFICATION"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class LoginResult:
    status: LoginStatus
    url: str
    message: str = ""


async def _first_visible(page: Page, strategy: SelectorStrategy) -> Locator | None:
    for label in strategy.labels:
        locator = page.get_by_label(label, exact=False)
        if await locator.count() and await locator.first.is_visible():
            return locator.first
    for placeholder in strategy.placeholders:
        locator = page.get_by_placeholder(placeholder, exact=False)
        if await locator.count() and await locator.first.is_visible():
            return locator.first
    if strategy.role:
        for name in strategy.names:
            locator = page.get_by_role(strategy.role, name=name, exact=False)
            if await locator.count() and await locator.first.is_visible():
                return locator.first
    for selector in strategy.selectors:
        locator = page.locator(selector)
        if await locator.count() and await locator.first.is_visible():
            return locator.first
    return None


async def open_login_page(page: Page, login_url: str) -> None:
    await page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)


async def verification_required(page: Page) -> bool:
    if await _first_visible(page, VERIFICATION):
        return True
    body = (await page.locator("body").inner_text()).lower()
    return any(term in body for term in ("captcha", "verification", "验证码", "滑块", "安全验证"))


async def is_authenticated(page: Page) -> bool:
    if "#/login" not in page.url and await _first_visible(page, AUTHENTICATED):
        return True
    login_form_present = bool(await _first_visible(page, LOGIN_PASSWORD))
    return "#/login" not in page.url and not login_form_present


async def login(
    page: Page,
    username: str,
    password: str,
    *,
    login_url: str,
    storage_state_path: Path,
    stage_callback: Callable[[LoginStatus], None] | None = None,
    screenshot_callback: Callable[[bytes], None] | None = None,
    verification_code_provider: Callable[[], str | None] | None = None,
    verification_timeout_seconds: float = 300.0,
) -> LoginResult:
    await open_login_page(page, login_url)
    username_input = await _first_visible(page, LOGIN_USERNAME)
    password_input = await _first_visible(page, LOGIN_PASSWORD)
    login_button = await _first_visible(page, LOGIN_BUTTON)
    if not username_input or not password_input or not login_button:
        return LoginResult(LoginStatus.FAILED, page.url, "Login controls were not found")
    await username_input.fill(username)
    await password_input.fill(password)
    await login_button.click()

    if await verification_required(page):
        if stage_callback:
            stage_callback(LoginStatus.WAITING_USER_VERIFICATION)
        if screenshot_callback:
            screenshot_callback(await page.screenshot(type="png"))
        deadline = asyncio.get_running_loop().time() + verification_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            code = verification_code_provider() if verification_code_provider else None
            if code:
                await _enter_verification_code(page, code)
            if await is_authenticated(page):
                break
            await asyncio.sleep(1.0)
        else:
            return LoginResult(LoginStatus.WAITING_USER_VERIFICATION, page.url, "Interactive verification was not completed")
    else:
        try:
            await page.wait_for_function("() => !location.hash.includes('/login')", timeout=30_000)
        except Exception:
            pass

    if not await is_authenticated(page):
        return LoginResult(LoginStatus.FAILED, page.url, "Authenticated CAMDS state was not detected")
    storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    await page.context.storage_state(path=storage_state_path)
    return LoginResult(LoginStatus.AUTHENTICATED, page.url, "CAMDS login successful")


async def _enter_verification_code(page: Page, code: str) -> bool:
    """Enter user-provided CAPTCHA text only; never derives or solves it."""
    field = await _first_visible(page, VERIFICATION_INPUT)
    if not field:
        return False
    await field.fill(code)
    submit = await _first_visible(page, VERIFICATION_SUBMIT)
    if submit:
        await submit.click()
    return True
