from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from playwright.async_api import async_playwright

from ..core.credentials import Credentials
from .login import LoginResult, LoginStatus, login


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
                )
            finally:
                await browser.close()
