from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from .operations import CREATE_URL


CLASSIFICATION_DIALOG = "Creation of a new material"

# Structure worth recording to decide how a classification is actually picked:
# a flat table of codes, or a tree that must be expanded level by level.
_DIALOG_ENTRIES = """el => Array.from(el.querySelectorAll(
    "td, th, li, [role='treeitem'], [role='row'], .el-tree-node__label, .el-tree-node__expand-icon"
)).map(n => ({tag: n.tagName, cls: n.getAttribute('class'), role: n.getAttribute('role'),
    text: (n.innerText || '').trim().slice(0, 200),
    expandable: !!n.querySelector('.el-tree-node__expand-icon, .el-table__expand-icon'),
    depth: (() => { let d = 0, p = n.parentElement; while (p && p !== el) { d++; p = p.parentElement; } return d; })()}))
    .filter(n => n.text)"""


async def capture_dialog(page: Page, dialog, target: Path) -> dict[str, Any]:
    """Record one modal's structure. Dialogs never change the URL, so the
    route-based discovery loop cannot see them."""
    target.mkdir(parents=True, exist_ok=True)
    entries = await dialog.evaluate(_DIALOG_ENTRIES)
    buttons = await dialog.get_by_role("button").evaluate_all(
        "els => els.map(el => ({text: (el.innerText || '').trim(), ariaLabel: el.getAttribute('aria-label')}))")
    (target / "dialog.html").write_text(await dialog.evaluate("el => el.outerHTML"), encoding="utf-8")
    await dialog.screenshot(path=target / "dialog.png")
    result = {"captured_at": datetime.now(timezone.utc).isoformat(), "url": page.url,
              "entries": entries, "buttons": buttons,
              "codes": sorted({e["text"].split(":")[0].strip() for e in entries
                               if re.fullmatch(r"\d+(?:\.\d+)*(?:\s*:.*)?", e["text"], re.S)})}
    (target / "dialog_snapshot.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


async def discover_material_classifications(operations, target: Path) -> dict[str, Any]:
    """Open the material classification wizard, record it, and close it.

    CAMDS allocates the MDS ID only when Next opens the editor, so this stops at
    the dialog and never presses Next: no MDS is created and no ID is consumed.
    """
    page = operations.page
    await operations._navigate(CREATE_URL)
    row = page.get_by_role("row").filter(has=page.get_by_role("cell", name="Materials", exact=True))
    await row.get_by_role("button", name="Create", exact=True).click()
    dialog = page.get_by_role("dialog", name=CLASSIFICATION_DIALOG, exact=True)
    await dialog.wait_for(timeout=30_000)
    try:
        snapshot = await capture_dialog(page, dialog, target)
    finally:
        await _close_without_next(page, dialog, operations)
    snapshot["note"] = ("Classification wizard recorded without pressing Next; no MDS was created. "
                        f"Snapshot written to {target}.")
    return snapshot


async def _close_without_next(page: Page, dialog, operations) -> None:
    """Dismiss the wizard. Next is never pressed: that is what allocates an ID."""
    closer = dialog.get_by_role("button", name=re.compile(r"^(Cancel|Close|取消|关闭)$")).or_(
        dialog.locator(".el-dialog__headerbtn"))
    if await closer.count():
        await closer.first.click()
    else:
        await page.keyboard.press("Escape")
    try:
        await dialog.wait_for(state="hidden", timeout=15_000)
    except Exception as exc:
        # Fail closed: an open wizard must not be mistaken for an idle session.
        operations.editor_open = True
        raise RuntimeError(
            "The classification wizard did not close. Nothing was created, but review and close it in the browser "
            "before running another operation.") from exc
    if await page.locator("text=CA_").count():
        raise RuntimeError("An MDS ID appeared during classification discovery; inspect the browser before continuing.")


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


