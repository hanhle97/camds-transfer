"""Search and prepare a single, unsaved MDS root using observed CAMDS controls.

No generic click/confirm API is exposed. Child insertion and persistence are not
implemented; preparing a root is deliberately reported separately from import.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
import asyncio

from playwright.async_api import Page, Locator, expect

BASE_URL = "https://catarc.camds.org.cn/"
SEARCH_URL = BASE_URL + "#/seek/seekMaterialDataSheet"
CREATE_URL = BASE_URL + "#/create/materialDataSheet"
KINDS = ("Component", "Semicomponent", "Material", "Basic Substance", "All MDSs")
NAME_LABELS = dict(zip(KINDS, ("Article Name:", "Article Name:", "Material Name:", "Name / Synonym / English Name:", "Material Name:")))
NUMBER_LABELS = {"Component": "Part/Item No.:", "Semicomponent": "Item- /Mat.-No.:", "Material": "Material No.:", "All MDSs": "Component No., Semicomponent No., Material No."}


def form_item(scope: Page | Locator, label: str) -> Locator:
    return scope.locator('.el-form-item:has(> .el-form-item__label:text-is(' + json.dumps(label, ensure_ascii=False) + '))')


@dataclass(frozen=True)
class SearchRequest:
    kind: str
    name: str = ""
    identifier: str = ""
    number: str = ""
    cas: str = ""
    source: str = "Own"

    def validate(self) -> None:
        if self.kind not in KINDS or self.source not in ("Own", "Published", "Accepted", "All"):
            raise ValueError("Unsupported search type or source")
        if not any((self.name.strip(), self.identifier.strip(), self.number.strip(), self.cas.strip())):
            raise ValueError("Enter at least one search criterion")
        if self.kind == "Basic Substance" and (self.identifier or self.number):
            raise ValueError("Basic Substance search supports name and CAS")
        if self.kind != "Basic Substance" and self.cas:
            raise ValueError("CAS is only supported by Basic Substance search")
        if any(len(value) > 50 for value in (self.name, self.identifier, self.number, self.cas)):
            raise ValueError("Search criteria must not exceed 50 characters")


@dataclass(frozen=True)
class CreateRequest:
    kind: str
    name: str
    number: str = ""
    weight_g: str = ""
    classification: str = ""
    remark: str = ""

    def validate(self) -> None:
        if self.kind not in KINDS[:3]:
            raise ValueError("Only Component, Semicomponent and Material roots are supported")
        if not self.name.strip() or len(self.name) > 100:
            raise ValueError("Name is required and must not exceed 100 characters")
        if len(self.number) > 50 or len(self.remark) > 2000:
            raise ValueError("Number must be at most 50 characters; remark at most 2000")
        if self.kind == "Material" and self.classification != "1.1.1":
            raise ValueError("Only Material classification 1.1.1 has been verified; other classifications are not yet supported")
        if self.kind != "Component" and self.weight_g:
            raise ValueError("Root mass is only supported for Component")
        if self.kind == "Component":
            try:
                mass = float(self.weight_g)
            except ValueError as exc:
                raise ValueError("Component mass in grams is required") from exc
            if not math.isfinite(mass) or mass <= 0 or len(self.weight_g) > 50:
                raise ValueError("Component mass must be finite and greater than zero")


class CamdsOperations:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.editor_open = False
        self.results_ready = False

    async def _navigate(self, url: str) -> None:
        if self.editor_open or "#/createComponent/" in self.page.url:
            raise RuntimeError("An MDS editor is open. Review it in the browser, then close this session before starting another operation.")
        if self.page.url == url:
            await self.page.reload(wait_until="domcontentloaded", timeout=60_000)
        else:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        # Explicit page controls, not the generic authenticated shell, gate actions.
        try:
            if url == SEARCH_URL:
                await self.page.get_by_role("tab", name="Component", exact=True).wait_for(timeout=15_000)
            else:
                await self.page.get_by_role("cell", name="Materials", exact=True).wait_for(timeout=15_000)
        except Exception as exc:
            raise RuntimeError("CAMDS page is not ready. Sign in and complete verification in the browser; use English, then retry.") from exc

    async def search(self, request: SearchRequest) -> dict:
        request.validate()
        self.results_ready = False
        await self._navigate(SEARCH_URL)
        await self.page.get_by_role("tab", name=request.kind, exact=True).click()
        await expect(self.page.get_by_role("tab", name=request.kind, exact=True)).to_have_attribute("aria-selected", "true")
        criteria = {NAME_LABELS[request.kind]: request.name}
        if request.kind == "Basic Substance":
            criteria["CAS No.:"] = request.cas
        else:
            criteria["MDS ID" if request.kind == "All MDSs" else "ID:"] = request.identifier
            criteria[NUMBER_LABELS[request.kind]] = request.number
        for label, value in criteria.items():
            await form_item(self.page, label).get_by_role("textbox").fill(value)
        if request.kind != "Basic Substance":
            for label, enabled in {
                "MDSs": request.source in ("Own", "All"),
                "Modules": request.source in ("Own", "All"),
                "Published MDSs": request.source in ("Published", "All"),
                "Accepted MDSs": request.source in ("Accepted", "All"),
            }.items():
                await self.page.get_by_role("checkbox", name=label, exact=True).set_checked(enabled)
        await self.page.get_by_role("button", name="Search", exact=True).click()
        await self.page.get_by_role("button", name="newSearch", exact=True).wait_for(timeout=60_000)
        await expect(self.page.locator(".el-loading-mask:visible")).to_have_count(0, timeout=60_000)
        self.results_ready = True
        return await self.read_results()

    async def read_results(self) -> dict:
        if not self.results_ready:
            raise RuntimeError("Run a search first")
        # Read one complete table; fixed-column clones must not duplicate results.
        tables = await self.page.locator("table.el-table__body").evaluate_all("""tables => tables.map(t =>
            Array.from(t.querySelectorAll('tbody > tr')).map(r =>
                Array.from(r.querySelectorAll('td')).map(c => c.innerText.trim())))""")
        headers = await self.page.locator("table.el-table__header").evaluate_all("""tables => tables.map(t =>
            Array.from(t.querySelectorAll('th')).map(c => c.innerText.trim()))""")
        rows = max(tables, key=lambda table: max((len(r) for r in table), default=0), default=[])
        columns = max(headers, key=len, default=[])
        # Action buttons are not business data. Keep alignment for any blank columns.
        indices = [i for i, h in enumerate(columns) if h and h != "Actions"]
        return {"kind": "search", "columns": [columns[i] for i in indices],
                "rows": [[row[i] if i < len(row) else "" for i in indices] for row in rows],
                "note": "Current result page only. Search again to refresh; no records modified."}

    async def create(self, request: CreateRequest) -> dict:
        request.validate()  # All local validation must precede ID allocation.
        await self._navigate(CREATE_URL)
        row_name = {"Component": "Component (including complete vehicles)", "Semicomponent": "Semi- Components", "Material": "Materials"}[request.kind]
        row = self.page.get_by_role("row").filter(has=self.page.get_by_role("cell", name=row_name, exact=True))
        self.editor_open = True  # Fail closed even if the click/next request times out.
        await row.get_by_role("button", name="Create", exact=True).click()
        if request.kind == "Material":
            dialog = self.page.get_by_role("dialog", name="Creation of a new material", exact=True)
            await dialog.get_by_role("cell", name=request.classification, exact=True).click()
            await dialog.get_by_role("button", name="Next", exact=True).click()
        details = self.page.get_by_role("tabpanel", name="Details", exact=True)
        await expect(form_item(details, "Type")).to_contain_text(request.kind, timeout=60_000)
        # The SPA initially renders a placeholder Component while fetching the root.
        identity = form_item(details, "ID / Version")
        await expect(identity).to_contain_text("CA_", timeout=60_000)
        name_label = "Material Name" if request.kind == "Material" else "Article Name"
        number_label = {"Component": "Component No.", "Semicomponent": "Semicomponent No.", "Material": "Material No."}[request.kind]
        values = {name_label: request.name, number_label: request.number}
        if request.kind == "Component":
            mass = form_item(details, "Measured Mass per Item")
            # Observed new Component default is g. Fail closed if site default changes.
            await expect(mass.get_by_role("button", name="g", exact=True)).to_be_visible()
            values["Measured Mass per Item"] = request.weight_g
        for label, value in values.items():
            control = form_item(details, label).get_by_role("textbox")
            await control.fill(value)
            await expect(control).to_have_value(value)
        remark = form_item(details, "Remark").locator("textarea")
        await remark.fill(request.remark)
        await expect(remark).to_have_value(request.remark)
        return {"kind": "create", "identity": (await identity.inner_text()).strip(),
                "note": "Root form filled and read back in the browser. Review it, then use Save in the app to persist."}

    async def save(self, request=None) -> dict:
        if not self.editor_open:
            raise RuntimeError("No open MDS editor to save")
        button = self.page.get_by_role("button", name="Save", exact=True)
        await expect(button).to_be_visible(timeout=15_000)
        await button.click()
        await asyncio.sleep(1)
        self.editor_open = False
        return {"kind": "save", "identity": "", "note": "MDS node saved in CAMDS. Review the browser confirmation."}
