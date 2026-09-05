from __future__ import annotations

import os
from pathlib import Path

import pytest

from camds_imds_importer.camds.browser import BrowserConfig, CamdsBrowser
from camds_imds_importer.camds.login import LoginStatus
from camds_imds_importer.core.credentials import Credentials


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_camds_login(tmp_path: Path) -> None:
    username = os.getenv("CAMDS_USERNAME")
    password = os.getenv("CAMDS_PASSWORD")
    if not username or not password:
        pytest.skip("CAMDS_USERNAME and CAMDS_PASSWORD are required")
    browser = CamdsBrowser(BrowserConfig(
        login_url="https://catarc.camds.org.cn/#/login",
        storage_state_path=tmp_path / "storage.json",
        headless=False,
    ))
    result = await browser.test_login(Credentials(username, password))
    assert result.status in {LoginStatus.AUTHENTICATED, LoginStatus.WAITING_USER_VERIFICATION}
