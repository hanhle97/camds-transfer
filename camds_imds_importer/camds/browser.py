from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from playwright.async_api import async_playwright

from ..core.credentials import Credentials
from .login import LoginResult, LoginStatus, login
from .discovery import capture_controls


@dataclass(slots=True)
class BrowserConfig:
    login_url: str
    storage_state_path: Path
    headless: bool = False
    slow_mo: int = 50


class CamdsBrowser:
    def __init__(self, config: BrowserConfig) -> None:
        self.config = config

    async def test_login(
        self,
        credentials: Credentials,
        stage_callback: Callable[[LoginStatus], None] | None = None,
        screenshot_callback: Callable[[bytes], None] | None = None,
        verification_code_provider: Callable[[], str | None] | None = None,
    ) -> LoginResult:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.config.headless, slow_mo=self.config.slow_mo)
            context = await browser.new_context()
            page = await context.new_page()
            try:
                return await login(
                    page, credentials.username, credentials.password,
                    login_url=self.config.login_url,
                    storage_state_path=self.config.storage_state_path,
                    stage_callback=stage_callback,
                    screenshot_callback=screenshot_callback,
                    verification_code_provider=verification_code_provider,
                )
            finally:
                await browser.close()

    async def discover_authenticated(self, target: Path, base_url: str) -> dict:
        """Open a persisted authenticated session and capture only read-only page metadata."""
        if not self.config.storage_state_path.is_file():
            raise FileNotFoundError("No authenticated storage state found; complete Test Login first")
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=self.config.headless, slow_mo=self.config.slow_mo)
            context = await browser.new_context(storage_state=self.config.storage_state_path)
            page = await context.new_page()
            try:
                await page.goto(base_url, wait_until="domcontentloaded", timeout=60_000)
                return await capture_controls(page, target)
            finally:
                await browser.close()
