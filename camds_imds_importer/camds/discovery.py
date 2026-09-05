from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Page


async def capture_controls(page: Page, target: Path) -> dict[str, Any]:
    target.mkdir(parents=True, exist_ok=True)
    controls = await page.locator("input, select, button, textarea, [role]").evaluate_all(
        """els => els.map(el => ({tag: el.tagName, role: el.getAttribute('role'), id: el.id,
        name: el.getAttribute('name'), type: el.getAttribute('type'), placeholder: el.getAttribute('placeholder'),
        text: (el.innerText || '').trim(), ariaLabel: el.getAttribute('aria-label')}))"""
    )
    (target / "dom.html").write_text(await page.content(), encoding="utf-8")
    (target / "controls.json").write_text(json.dumps(controls, indent=2, ensure_ascii=False), encoding="utf-8")
    await page.screenshot(path=target / "screenshot.png", full_page=True)
    result = {"captured_at": datetime.now(timezone.utc).isoformat(), "url": page.url, "title": await page.title(), "controls": controls}
    (target / "snapshot.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
