"""Draft-only tree import with an append-only operation journal and final read-back."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from datetime import datetime, timezone

from playwright.async_api import expect

from .import_plan import ImportRequest, proportion
from .operations import CREATE_URL, SEARCH_URL, CreateRequest, form_item


def number(value):
    return format(float(value), ".12g")


class ImportJournal:
    def __init__(self, request: ImportRequest, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (request.fingerprint + ".jsonl")
        # An interrupted Create/Save must never be retried blindly.
        with self.path.open("x", encoding="utf-8") as out:
            out.write(json.dumps({"event": "started", "fingerprint": request.fingerprint}) + "\n")

    def record(self, event, **fields):
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, ensure_ascii=False) + "\n")


class DraftBrowser:
    def __init__(self, operations):
        self.ops = operations
        self.page = operations.page

    async def prepare(self):
        await self.ops._navigate(SEARCH_URL)
        await self.settled()

    async def settled(self):
        # Loading can start a rendering tick after a click; wait for a stable DOM.
        for _ in range(2):
            await asyncio.sleep(0.35)
            await expect(self.page.locator('.el-loading-mask:visible')).to_have_count(0, timeout=60_000)

    def details(self):
        return self.page.get_by_role("tabpanel", name="Details", exact=True)

    async def text(self, label):
        return (await form_item(self.details(), label).inner_text()).strip()

    async def value(self, label):
        return await form_item(self.details(), label).get_by_role("textbox").input_value()

    async def fill(self, label, value):
        control = form_item(self.details(), label).get_by_role("textbox")
        await control.fill(value)
        await control.press("Tab")
        await expect(control).to_have_value(value)

    async def identity(self):
        match = re.search(r"(CA_\d+_\d+)\s*/\s*(\d+(?:\.\d+)?)", await self.text("ID / Version"))
        if not match:
            raise RuntimeError("CAMDS ID/version is missing")
        return tuple(match.groups())

    async def create_root(self, node):
        if self.ops.editor_open or "#/createComponent/" in self.page.url:
            await self.leave(CREATE_URL)
        await self.ops.create(CreateRequest(
            "Material" if node["node_type"] == "MATERIAL" else "Component", node["name"],
            (node.get("material_number") if node["node_type"] == "MATERIAL" else node.get("part_number")) or "",
            number(node["weight_g"]) if node["node_type"] == "COMPONENT" else "",
            node.get("classification") or "", "Imported from parsed IMDS; draft for review."))
        await self.settled()
        return await self.identity()

    async def save(self):
        await self.ops.save()

    async def leave(self, url):
        # Never accept an unsaved-data warning or save-confirmation automatically.
        await self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await self.settled()
        if await self.page.get_by_role("dialog").count():
            raise RuntimeError("CAMDS blocked navigation with a dialog; previous Save is unconfirmed. Review browser.")
        expected = (self.page.get_by_role("tab", name="Component", exact=True) if url == SEARCH_URL
                    else self.page.get_by_role("cell", name="Materials", exact=True))
        await expected.wait_for(timeout=30_000)
        self.ops.editor_open = False

    async def open_saved(self, kind, ref):
        await self.leave(SEARCH_URL)
        await self.page.get_by_role("tab", name=kind, exact=True).click()
        await self.settled()
        await expect(self.page.get_by_role("tab", name=kind, exact=True)).to_have_attribute("aria-selected", "true")
        await form_item(self.page, "ID:").get_by_role("textbox").fill(ref[0])
        await form_item(self.page, "version").get_by_role("textbox").fill(ref[1])
        for label in ("MDSs", "Modules", "Published MDSs", "Accepted MDSs"):
            await self.page.get_by_role("checkbox", name=label, exact=True).set_checked(True)
        await self.page.get_by_role("button", name="Search", exact=True).click()
        await self.page.get_by_role("button", name="newSearch", exact=True).wait_for(timeout=60_000)
        await self.settled()
        await expect(self.page.get_by_role("cell", name="/".join(ref), exact=True)).to_have_count(1)
        # Search fixed action column has one View per result, separate from data row.
        await expect(self.page.get_by_role("button", name="View", exact=True)).to_have_count(1)
        await self.page.get_by_role("button", name="View", exact=True).click()
        await self.settled()
        await expect(form_item(self.details(), "ID / Version")).to_contain_text(ref[0], timeout=30_000)
        if await self.identity() != tuple(ref):
            raise RuntimeError("Wrong saved version opened")
        name_label = "Material Name" if kind == "Material" else "Article Name"
        await expect(form_item(self.details(), name_label).get_by_role("textbox")).to_be_disabled()
        await self.page.get_by_role("img", name="Open Tree", exact=True).click()
        await self.settled()

    @staticmethod
    def tree_selector(path):
        return ' > ul > '.join('li[treenode]:has(> a > .node_name:text-is(' + json.dumps(name) + '))' for name in path)

    async def tree_node(self, path):
        # One compound selector avoids :scope/:has scoping ambiguity in chained locators.
        node = self.page.locator(self.tree_selector(path))
        await expect(node).to_have_count(1)
        return node

    async def select(self, path):
        node = await self.tree_node(path)
        await self.page.locator(self.tree_selector(path) + ' > a > .node_name').click()
        await self.settled()
        await expect(self.page.locator(self.tree_selector(path) + ' > a')).to_have_class(re.compile(r"curSelectedNode"))
        name = form_item(self.details(), "Article Name").get_by_role("textbox").or_(
            form_item(self.details(), "Material Name").get_by_role("textbox"))
        await expect(name).to_have_value(path[-1], timeout=60_000)

    async def verify_child_count(self, path, count):
        node = await self.tree_node(path)
        await expect(self.page.locator(self.tree_selector(path) + ' > ul > li[treenode]')).to_have_count(count)

    async def add_component(self, parent_path, child):
        await self.select(parent_path)
        await self.page.locator('img[title="Add Component"]').click()
        await self.settled()
        await expect(form_item(self.details(), "Quantity")).to_be_visible()
        await self.fill("Article Name", child["name"])
        await self.fill("Component No.", child.get("part_number") or "")
        await expect(form_item(self.details(), "Measured Mass per Item").get_by_role("button", name="g", exact=True)).to_be_visible()
        await self.fill("Measured Mass per Item", number(child["weight_g"]))
        await self.fill("Quantity", number(child["quantity"]))

    async def lookup_dialog(self, icon, label, value, result_value, version=None):
        await self.page.locator(f'img[title="{icon}"]').click()
        dialog = self.page.get_by_role("dialog", name="Detail", exact=True)
        await dialog.wait_for()
        await self.settled()
        await form_item(dialog, label).get_by_role("textbox").fill(value)
        if version:
            await form_item(dialog, "version").get_by_role("textbox").fill(version)
            for name in ("MDSs", "Modules", "Published MDSs", "Accepted MDSs"):
                await dialog.get_by_role("checkbox", name=name, exact=True).set_checked(True)
        await dialog.get_by_role("button", name="Search", exact=True).click()
        await dialog.get_by_role("button", name="newSearch", exact=True).wait_for(timeout=60_000)
        await self.settled()
        row = dialog.get_by_role("row").filter(has=self.page.get_by_role("cell", name=result_value, exact=True))
        await expect(row).to_have_count(1)
        await row.click()
        # Only this explicit result-selection Confirm is permitted.
        await dialog.get_by_role("button", name="Confirm", exact=True).click()
        await dialog.wait_for(state="hidden", timeout=30_000)
        await self.settled()

    async def add_material(self, parent_path, node, ref):
        await self.select(parent_path)
        await self.lookup_dialog("Add Metarial", "ID:", ref[0], "/".join(ref), ref[1])
        if await self.identity() != tuple(ref):
            raise RuntimeError("Attached Material ID/version mismatch")
        await expect(form_item(self.details(), "Mass").get_by_role("button", name="g", exact=True)).to_be_visible()
        await self.fill("Mass", number(node["weight_g"]))
        return await self.value("Material Name")

    async def add_substance(self, root_name, node):
        await self.select([root_name])
        await self.lookup_dialog("Add Substance", "CAS No.:", node["cas_number"], node["cas_number"])
        if node["cas_number"] not in await self.text("CAS No."):
            raise RuntimeError("Substance CAS mismatch")
        mode = proportion(node)
        value = {"range": "1", "fixed": "2", "rest": "3"}[mode[0]]
        radio = form_item(self.details(), "Proportion").locator(f'label[role="radio"]:has(input[value="{value}"])')
        await radio.click()
        await expect(radio).to_have_attribute("aria-checked", "true")
        for index, quantity in enumerate(mode[1:]):
            control = radio.locator('input[type="text"]').nth(index)
            await control.fill(number(quantity))
            await control.press("Tab")
        return (await self.page.locator('a.curSelectedNode > .node_name').inner_text()).strip()

    async def verify_value(self, label, expected):
        actual = await self.value(label)
        if isinstance(expected, (int, float)):
            import math
            if not math.isclose(float(actual), expected, rel_tol=1e-8, abs_tol=1e-8):
                raise RuntimeError(f"Read-back mismatch: {label}: {actual} != {expected}")
        elif actual != expected:
            raise RuntimeError(f"Read-back mismatch: {label}")

    async def verify_substance(self, path, node):
        # CAMDS can switch substance tree labels between Chinese and English
        # after Save. Match the stable CAS, not the display name.
        parent = await self.tree_node(path[:-1])
        children = self.page.locator(self.tree_selector(path[:-1]) + ' > ul > li[treenode] > a')
        found = False
        for index in range(await children.count()):
            display_name = (await children.nth(index).locator('.node_name').inner_text()).strip()
            await children.nth(index).click()
            await self.settled()
            await expect(self.details()).to_contain_text(display_name, timeout=60_000)
            if node["cas_number"] in await self.text("CAS No."):
                found = True
                break
        if not found:
            raise RuntimeError("Saved Substance CAS missing")
        mode = proportion(node)
        # Read-only View omits native radio inputs and changes accessible names.
        radio = form_item(self.details(), "Proportion").get_by_role("radio", name=re.compile({"fixed": r"^Fixed", "range": r"^From", "rest": r"^Rest"}[mode[0]]))
        await expect(radio).to_have_attribute("aria-checked", "true")
        inputs = radio.get_by_role("textbox")
        await expect(inputs).to_have_count(len(mode) - 1)
        for index, value in enumerate(mode[1:]):
            if abs(float(await inputs.nth(index).input_value()) - value) > 1e-8:
                raise RuntimeError("Saved Substance proportion mismatch")


class TreeImporter:
    def __init__(self, backend, directory=Path("output/camds_imports"), progress=lambda text: None):
        self.io, self.directory, self.progress = backend, directory, progress

    async def run(self, request: ImportRequest):
        request = request.snapshot()
        request.validate()
        # Login/readiness failures should not create a duplicate-prevention journal.
        await self.io.prepare()
        try:
            journal = ImportJournal(request, self.directory)
        except FileExistsError as exc:
            raise RuntimeError("This import already has a journal. Inspect output/camds_imports and saved IDs before retrying; automatic replay is blocked.") from exc
        refs = dict(request.material_refs)
        names = {}
        def record(event, **fields):
            journal.record(event, **fields)
            messages = {"save_requested": "Saving draft", "save_returned_pending_readback": "Checking next step",
                        "create_material_requested": "Creating Material", "material_id_allocated": "Material ID assigned",
                        "add_substance_requested": "Adding Substance", "existing_material_verified": "Existing Material verified",
                        "material_readback_verified": "Saved Material verified", "create_parent_requested": "Creating parent Component",
                        "parent_id_allocated": "Parent ID assigned", "add_child_requested": "Adding child Component",
                        "attach_material_requested": "Attaching Material", "complete_readback_verified": "Saved tree verified"}
            self.progress(messages.get(event, event) + (": " + fields["name"] if "name" in fields else ""))
        async def save(uid):
            record("save_requested", uid=uid)
            await self.io.save()
            record("save_returned_pending_readback", uid=uid)
        try:
            for mat in request.materials():
                if mat["uid"] in refs:
                    # Verify mapped reference exists before creating anything dependent.
                    await self.io.open_saved("Material", refs[mat["uid"]])
                    names[mat["uid"]] = await self.io.value("Material Name")
                    record("existing_material_verified", uid=mat["uid"], ref=refs[mat["uid"]])
                    continue
                record("create_material_requested", uid=mat["uid"], name=mat["name"])
                ref = await self.io.create_root(mat)
                refs[mat["uid"]] = ref
                record("material_id_allocated", uid=mat["uid"], ref=ref)
                await save(mat["uid"])
                substance_names = {}
                # Rest is attached last so other portions are already present.
                for substance in sorted(mat["children"], key=lambda n: bool(n.get("is_rest"))):
                    record("add_substance_requested", uid=substance["uid"], name=substance["name"])
                    substance_names[substance["uid"]] = await self.io.add_substance(mat["name"], substance)
                    await save(substance["uid"])
                await self.io.open_saved("Material", ref)
                await self.io.verify_value("Material Name", mat["name"])
                await self.io.verify_value("Material No.", mat.get("material_number") or "")
                await self.io.verify_child_count([mat["name"]], len(mat["children"]))
                for substance in mat["children"]:
                    await self.io.verify_substance([mat["name"], substance_names[substance["uid"]]], substance)
                names[mat["uid"]] = mat["name"]
                record("material_readback_verified", uid=mat["uid"], ref=ref)
            root = request.root
            if root["node_type"] == "MATERIAL":
                root_ref = refs[root["uid"]]
            else:
                def check_names(node):
                    children = node.get("children", [])
                    labels = [names[c["uid"]] if c["node_type"] == "MATERIAL" else c["name"] for c in children]
                    if len(labels) != len(set(labels)):
                        raise RuntimeError("Mapped Materials have duplicate sibling names; automatic tree selection is ambiguous")
                    for child in children:
                        if child["node_type"] == "COMPONENT":
                            check_names(child)
                check_names(root)
                record("create_parent_requested", uid=root["uid"], name=root["name"])
                root_ref = await self.io.create_root(root)
                record("parent_id_allocated", uid=root["uid"], ref=root_ref)
                await save(root["uid"])
                async def build(node, path):
                    for child in node["children"]:
                        if child["node_type"] == "COMPONENT":
                            record("add_child_requested", uid=child["uid"], name=child["name"])
                            await self.io.add_component(path, child)
                            await save(child["uid"])
                            await build(child, path + [child["name"]])
                        else:
                            record("attach_material_requested", uid=child["uid"], ref=refs[child["uid"]])
                            names[child["uid"]] = await self.io.add_material(path, child, refs[child["uid"]])
                            await save(child["uid"])
                await build(root, [root["name"]])
                await self.io.open_saved("Component", root_ref)
                async def verify(node, path):
                    await self.io.select(path)
                    await self.io.verify_child_count(path, len(node["children"]))
                    await self.io.verify_value("Article Name", node["name"])
                    await self.io.verify_value("Component No.", node.get("part_number") or "")
                    await self.io.verify_value("Measured Mass per Item", float(node["weight_g"]))
                    if len(path) > 1:
                        await self.io.verify_value("Quantity", float(node["quantity"]))
                    for child in node["children"]:
                        if child["node_type"] == "COMPONENT":
                            await verify(child, path + [child["name"]])
                        else:
                            await self.io.select(path + [names[child["uid"]]])
                            if await self.io.identity() != tuple(refs[child["uid"]]):
                                raise RuntimeError("Saved Material reference mismatch")
                            await self.io.verify_value("Mass", float(child["weight_g"]))
                await verify(root, [root["name"]])
            record("complete_readback_verified", root_ref=root_ref, material_refs=refs)
            return {"kind": "import_tree", "identity": "/".join(root_ref), "editor_open": False,
                    "note": "Draft tree saved and verified through Search / View. No Send/Submit. Journal: " + str(journal.path)}
        except BaseException as exc:
            journal.record("interrupted_or_failed", error=str(exc), material_refs=refs)
            raise
