"""Draft-only tree import with an append-only operation journal and final read-back."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import re
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone

from playwright.async_api import expect

from .application_mapping import ApplicationMapping, Resolution, normalise
from .import_control import ImportControl, ImportStopped, Reporter
from .import_plan import ImportRequest, proportion, real_cas
from .operations import CREATE_URL, NAVIGATION_TIMEOUT_MS, SEARCH_URL, CreateRequest, form_item


# Inferred from the Basic Substance search, which this dialog reuses; the
# recorded evidence covers only the CAS field, so a missing label fails loudly.
SUBSTANCE_NAME_LABEL = "Name / Synonym / English Name:"


def number(value):
    return format(float(value), ".12g")


@dataclass
class ResumeState:
    """What a previous run of the same fingerprint durably established."""

    material_refs: dict = dataclass_field(default_factory=dict)
    names: dict = dataclass_field(default_factory=dict)
    completed: set = dataclass_field(default_factory=set)
    incomplete_materials: dict = dataclass_field(default_factory=dict)
    root_ref: tuple | None = None
    finished: bool = False


def read_journal(path: Path) -> ResumeState:
    """Reconstruct durable state from an append-only journal, ignoring partial lines."""
    state = ResumeState()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            # A crash can truncate the final line; earlier entries stay authoritative.
            continue
        event, uid = entry.get("event"), entry.get("uid")
        ref = tuple(entry["ref"]) if entry.get("ref") else None
        if event == "material_id_allocated" and uid:
            state.incomplete_materials[uid] = ref
        elif event in ("material_readback_verified", "existing_material_verified") and uid:
            state.completed.add(uid)
            state.incomplete_materials.pop(uid, None)
            if ref:
                state.material_refs[uid] = ref
            if entry.get("display_name"):
                state.names[uid] = entry["display_name"]
        elif event == "parent_id_allocated":
            state.root_ref = ref
        elif event == "complete_readback_verified":
            state.finished = True
    return state


class ImportJournal:
    def __init__(self, path: Path, *, fingerprint: str | None = None):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if fingerprint is not None:
            # An interrupted Create/Save must never be retried blindly.
            with path.open("x", encoding="utf-8") as out:
                out.write(json.dumps({"event": "started", "fingerprint": fingerprint}) + chr(10))

    def record(self, event, **fields):
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "event": event, **fields}, ensure_ascii=False) + chr(10))


class DraftBrowser:
    def __init__(self, operations):
        self.ops = operations
        self.page = operations.page
        self.reporter = None

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
        if self.reporter:
            self.reporter("field", field_label=label)
        control = form_item(self.details(), label).get_by_role("textbox")
        await control.fill(value)
        await control.press("Tab")
        await expect(control).to_have_value(value)

    async def identity(self):
        match = re.search(r"(CA_\d+_\d+)\s*/\s*(\d+(?:\.\d+)?)", await self.text("ID / Version"))
        if not match:
            raise RuntimeError("CAMDS ID/version is missing")
        return tuple(match.groups())

    async def create_root(self, node, on_allocated=None):
        if self.ops.editor_open or "#/createComponent/" in self.page.url:
            await self.leave(CREATE_URL)
        await self.ops.create(CreateRequest(
            "Material" if node["node_type"] == "MATERIAL" else "Component", node["name"],
            (node.get("material_number") if node["node_type"] == "MATERIAL" else node.get("part_number")) or "",
            number(node["weight_g"]) if node["node_type"] == "COMPONENT" else "",
            node.get("classification") or "", "Imported from parsed IMDS; draft for review."))
        await self.settled()
        reference = await self.identity()
        # CAMDS shows the id as soon as the form opens, so it is already spent.
        if on_allocated:
            on_allocated(reference)
        return reference

    async def save(self):
        await self.ops.save()

    async def leave(self, url):
        # Never accept an unsaved-data warning or save-confirmation automatically.
        await self.page.goto(url, wait_until="commit", timeout=NAVIGATION_TIMEOUT_MS)
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

    async def tree_node(self, path, at=(0, 1)):
        # One compound selector avoids :scope/:has scoping ambiguity in chained locators.
        # Repeated names are legitimate (a board really does carry 30 parts named
        # "Resistor"), so the path is resolved to the expected number of matches
        # and addressed by document order, which is the order they were added.
        occurrence, total = at
        node = self.page.locator(self.tree_selector(path))
        await expect(node).to_have_count(total)
        return node.nth(occurrence)

    async def select(self, path, at=(0, 1)):
        occurrence, total = at
        await self.tree_node(path, at)
        await self.page.locator(self.tree_selector(path) + ' > a > .node_name').nth(occurrence).click()
        await self.settled()
        await expect(self.page.locator(self.tree_selector(path) + ' > a').nth(occurrence)).to_have_class(re.compile(r"curSelectedNode"))
        name = form_item(self.details(), "Article Name").get_by_role("textbox").or_(
            form_item(self.details(), "Material Name").get_by_role("textbox"))
        await expect(name).to_have_value(path[-1], timeout=60_000)

    async def verify_child_count(self, path, count, at=(0, 1)):
        node = await self.tree_node(path, at)
        await expect(node.locator('xpath=./ul/li[@treenode]')).to_have_count(count)

    async def add_component(self, parent_path, child, at=(0, 1)):
        await self.select(parent_path, at)
        await self.page.locator('img[title="Add Component"]').click()
        await self.settled()
        await expect(form_item(self.details(), "Quantity")).to_be_visible()
        await self.fill("Article Name", child["name"])
        await self.fill("Component No.", child.get("part_number") or "")
        await expect(form_item(self.details(), "Measured Mass per Item").get_by_role("button", name="g", exact=True)).to_be_visible()
        await self.fill("Measured Mass per Item", number(child["weight_g"]))
        await self.fill("Quantity", number(child["quantity"]))

    async def add_semicomponent(self, parent_path, child, at=(0, 1)):
        """Insert a Semicomponent under the selected Component.

        Verified control: img[title="Add SemiComponent"] (alt 添加半成品部件).
        It inserts and selects the node directly, with no reference dialog.
        CAMDS shows no Quantity here, and stale details from the previously
        selected node stay visible for a moment, so the Type is checked first.
        """
        await self.select(parent_path, at)
        await self.page.locator('img[title="Add SemiComponent"]').click()
        await self.settled()
        await expect(form_item(self.details(), "Type")).to_contain_text("Semicomponent", timeout=60_000)
        await self.fill("Article Name", child["name"])
        await self.fill("Semicomponent No.", child.get("part_number") or "")
        # Observed units are mg/g/kg; fail closed if g is not the active one.
        await expect(form_item(self.details(), "Mass").get_by_role("button", name="g", exact=True)).to_be_visible()
        await self.fill("Mass", number(child["weight_g"]))

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

    async def add_material(self, parent_path, node, ref, at=(0, 1), by_portion=False):
        """Attach an existing Material to the selected parent.

        CAMDS asks for a different quantity depending on the parent: a Material
        under a Component carries a Mass in grams, the same Material under a
        Semicomponent carries a Proportion.
        """
        await self.select(parent_path, at)
        await self.lookup_dialog("Add Metarial", "ID:", ref[0], "/".join(ref), ref[1])
        if await self.identity() != tuple(ref):
            raise RuntimeError("Attached Material ID/version mismatch")
        if by_portion:
            await self.set_proportion(node)
        else:
            await expect(form_item(self.details(), "Mass").get_by_role("button", name="g", exact=True)).to_be_visible()
            await self.fill("Mass", number(node["weight_g"]))
        return await self.value("Material Name")

    async def add_substance(self, root_name, node):
        await self.select([root_name])
        cas = real_cas(node)
        if cas:
            await self.lookup_dialog("Add Substance", "CAS No.:", cas, cas)
            if cas not in await self.text("CAS No."):
                raise RuntimeError("Substance CAS mismatch")
        else:
            # System groups such as "Misc., not to declare" carry no CAS, so the
            # name is the only identifier. lookup_dialog requires exactly one
            # exact-name row, so an ambiguous name fails instead of guessing.
            await self.lookup_dialog("Add Substance", SUBSTANCE_NAME_LABEL, node["name"], node["name"])
        await self.set_proportion(node)
        return (await self.page.locator('a.curSelectedNode > .node_name').inner_text()).strip()

    async def set_proportion(self, node):
        """Enter From-To / Fixed / Rest on the selected node.

        The same widget carries a Substance portion inside a Material and a
        Material portion inside a Semicomponent: radio values 1, 2 and 3.
        """
        mode = proportion(node)
        value = {"range": "1", "fixed": "2", "rest": "3"}[mode[0]]
        radio = form_item(self.details(), "Proportion").locator(f'label[role="radio"]:has(input[value="{value}"])')
        await radio.click()
        await expect(radio).to_have_attribute("aria-checked", "true")
        for index, quantity in enumerate(mode[1:]):
            control = radio.locator('input[type="text"]').nth(index)
            await control.fill(number(quantity))
            await control.press("Tab")

    async def application_options(self, substance_name):
        """Open one substance row's Application dialog and read its live options.

        Options depend on the substance and change, so they are read from CAMDS
        rather than from a stored catalogue.
        """
        await self.page.get_by_role("tab", name="Application", exact=True).click()
        await self.settled()
        panel = self.page.get_by_role("tabpanel", name="Application", exact=True)
        row = panel.get_by_role("row").filter(
            has=self.page.get_by_role("cell", name=substance_name, exact=True))
        await expect(row).to_have_count(1)
        # The Application cell is a coloured span, not a textbox or a button.
        await row.locator('td > div.cell > span > font[color="blue"]').click()
        dialog = self.page.get_by_role("dialog", name="prohibited substance application standard", exact=True)
        await dialog.wait_for(timeout=30_000)
        await self.settled()
        options = await dialog.locator("label.el-radio").evaluate_all(
            """els => els.map(el => ({value: (el.querySelector('input') || {}).value,
                                      label: (el.innerText || '').trim(),
                                      checked: el.getAttribute('aria-checked') === 'true'}))""")
        return dialog, [o for o in options if o.get("value")]

    async def apply_application(self, dialog, resolution):
        """Select one recorded option and confirm it."""
        radio = dialog.locator(f'label.el-radio:has(input[value="{resolution.value}"])')
        await expect(radio).to_have_count(1)
        # A default may already be checked; that is not proof it was applied.
        await radio.click()
        await expect(radio).to_have_attribute("aria-checked", "true")
        await dialog.get_by_role("button", name="Confirm", exact=True).click()
        await dialog.wait_for(state="hidden", timeout=30_000)
        await self.settled()

    async def cancel_application(self, dialog):
        await dialog.get_by_role("button", name="Cancel", exact=True).click()
        await dialog.wait_for(state="hidden", timeout=30_000)
        await self.settled()

    async def read_back_findings(self) -> list[str]:
        """Differences accepted during read-back. This backend compares only
        what it can read from the form, and every such check either matches or
        raises, so it never has any."""
        return []

    async def verify_proportion(self, node, what="Substance"):
        """Read back From-To / Fixed / Rest. The read-only View omits the native
        radio inputs and renames the accessible options."""
        mode = proportion(node)
        radio = form_item(self.details(), "Proportion").get_by_role(
            "radio", name=re.compile({"fixed": r"^Fixed", "range": r"^From", "rest": r"^Rest"}[mode[0]]))
        await expect(radio).to_have_attribute("aria-checked", "true")
        inputs = radio.get_by_role("textbox")
        await expect(inputs).to_have_count(len(mode) - 1)
        for index, value in enumerate(mode[1:]):
            if abs(float(await inputs.nth(index).input_value()) - value) > 1e-8:
                raise RuntimeError(f"Saved {what} proportion mismatch")

    async def verify_value(self, label, expected):
        actual = await self.value(label)
        if isinstance(expected, (int, float)):
            import math
            if not math.isclose(float(actual), expected, rel_tol=1e-8, abs_tol=1e-8):
                raise RuntimeError(f"Read-back mismatch: {label}: {actual} != {expected}")
        elif actual != expected:
            raise RuntimeError(f"Read-back mismatch: {label}")

    async def verify_substance(self, path, node, at=(0, 1)):
        # CAMDS can switch substance tree labels between Chinese and English
        # after Save. Match the stable CAS where there is one; a system group
        # has none, so it is matched on the name CAMDS itself stored.
        cas = real_cas(node)
        children = self.page.locator(self.tree_selector(path[:-1]) + ' > ul > li[treenode] > a')
        found = False
        for index in range(await children.count()):
            display_name = (await children.nth(index).locator('.node_name').inner_text()).strip()
            await children.nth(index).click()
            await self.settled()
            await expect(self.details()).to_contain_text(display_name, timeout=60_000)
            if cas:
                if cas in await self.text("CAS No."):
                    found = True
                    break
            elif display_name.casefold() == path[-1].casefold():
                found = True
                break
        if not found:
            raise RuntimeError("Saved Substance CAS missing" if cas else
                               f"Saved Substance {path[-1]!r} missing")
        await self.verify_proportion(node)


class TreeImporter:
    def __init__(self, backend, directory=Path("output/camds_imports"), progress=lambda event: None,
                 control=None, mapping=None):
        self.io, self.directory, self.progress = backend, directory, progress
        self.control = control if control is not None else ImportControl()
        self.mapping = mapping if mapping is not None else ApplicationMapping()
        # Applications are always matched by wording; anything unclear is skipped.
        self.resolve_by_name = True
        self.skipped: list[str] = []

    def _open_journal(self, request, resume):
        """Return (journal, ResumeState). Refuses any re-entry that cannot be made safe."""
        path = self.directory / (request.fingerprint + ".jsonl")
        if not path.exists():
            return ImportJournal(path, fingerprint=request.fingerprint), ResumeState()
        state = read_journal(path)
        if not resume:
            raise RuntimeError(
                "This import already has a journal. Inspect output/camds_imports and saved IDs before retrying; "
                "automatic replay is blocked. Choose Resume to continue from verified Materials only.")
        if state.finished:
            raise RuntimeError("This import already completed and was verified through Search / View; nothing to resume.")
        # A draft editor cannot be re-entered after the browser is gone, so a
        # half-built Material or parent must not be silently rebuilt.
        if state.incomplete_materials:
            listed = ", ".join(uid + " = " + ("/".join(ref) if ref else "no ID recorded")
                               for uid, ref in sorted(state.incomplete_materials.items()))
            raise RuntimeError(
                "Cannot resume: these Materials were created but never verified: " + listed + ". "
                "Re-entering a saved draft editor needs an open-saved-MDS-for-editing flow that has not been "
                "discovered against CAMDS. Finish or discard them in CAMDS, then map them as existing Material "
                "references and start a new import.")
        if state.root_ref is not None:
            raise RuntimeError(
                "Cannot resume: the parent Component " + "/".join(state.root_ref) + " was already created. "
                "Re-entering a saved draft editor needs an open-saved-MDS-for-editing flow that has not been "
                "discovered against CAMDS. Complete this tree manually in CAMDS.")
        return ImportJournal(path), state

    async def set_applications(self, material, record, gate, reporter):
        """Apply each substance application on the Material's Application tab."""
        pending = [c for c in material.get("children", [])
                   if c.get("application_text") or c.get("application_id")]
        for substance in pending:
            await gate()
            reporter.step(substance["uid"], substance["name"], "SUBSTANCE", (material["name"],))
            record("application_requested", uid=substance["uid"], name=substance["name"],
                   imds_application=substance.get("application_text"))
            dialog, options = await self.io.application_options(substance["name"])
            resolution = self.mapping.resolve(substance["name"], substance.get("application_text"))
            if resolution is None and self.resolve_by_name:
                resolution = self.mapping.match_by_name(substance.get("application_text"), options)
            chosen = None
            if resolution is not None:
                chosen = next((o for o in options if str(o["value"]) == str(resolution.value)), None)
                if chosen is None:
                    resolution = None   # the approved option is no longer offered
                elif resolution.source == "reviewed" and resolution.label and \
                        normalise(chosen["label"]) != normalise(resolution.label):
                    resolution = chosen = None   # its wording changed since approval
            if resolution is None or chosen is None:
                # Skip rather than guess. An unset application is visibly missing;
                # a wrong one is a false regulatory statement that looks correct.
                await self.io.cancel_application(dialog)
                self.skipped.append(
                    f"{material['name']} / {substance['name']}: "
                    f"{substance.get('application_text')!r} matched none of the "
                    f"{len(options)} option(s) CAMDS offered; left unset")
                record("application_skipped", uid=substance["uid"], name=substance["name"],
                       imds_application=substance.get("application_text"),
                       offered=[o.get("label") for o in options])
                reporter("application_skipped")
                continue
            await self.io.apply_application(dialog, resolution)
            self.mapping.record(substance["name"], substance.get("application_text"),
                                Resolution(str(resolution.value), chosen["label"], resolution.source))
            self.mapping.save()
            record("application_applied", uid=substance["uid"], name=substance["name"],
                   imds_application=substance.get("application_text"),
                   camds_value=str(resolution.value), camds_label=chosen["label"],
                   source=resolution.source)

    async def run(self, request: ImportRequest, resume: bool = False):
        request = request.snapshot()
        self.skipped = []
        warnings = request.validate(self.mapping)
        plan = request.plan()
        # Login/readiness failures should not create a duplicate-prevention journal.
        await self.io.prepare()
        journal, state = self._open_journal(request, resume)
        reporter = Reporter(len(plan), self.progress)
        # Field-level progress is emitted by the backend while it fills a form.
        setattr(self.io, "reporter", reporter)
        refs = dict(request.material_refs)
        refs.update(state.material_refs)
        names = dict(state.names)
        done_uids = set(state.completed)

        def record(event, **fields):
            journal.record(event, **fields)
            reporter(event, ref=fields.get("ref"), name=fields.get("name"))

        async def gate():
            await self.control.wait(on_pause=lambda: reporter("paused"))

        async def save(uid):
            record("save_requested", uid=uid)
            await self.io.save()
            record("save_returned_pending_readback", uid=uid)

        if request.merges or warnings:
            # Recorded next to the allocated IDs so the run can be audited later.
            journal.record("accepted_with_findings", merges=request.merges, warnings=warnings)
        if resume:
            record("resumed", completed=sorted(done_uids), material_refs={k: list(v) for k, v in refs.items()})
        try:
            for mat in request.materials():
                await gate()
                reporter.step(mat["uid"], mat["name"], "MATERIAL")
                if mat["uid"] in done_uids:
                    reporter.done()
                    reporter("skipped_completed")
                    continue
                if mat["uid"] in refs:
                    # Verify mapped reference exists before creating anything dependent.
                    await self.io.open_saved("Material", refs[mat["uid"]])
                    names[mat["uid"]] = await self.io.value("Material Name")
                    reporter.done()
                    record("existing_material_verified", uid=mat["uid"], ref=refs[mat["uid"]],
                           display_name=names[mat["uid"]])
                    continue
                record("create_material_requested", uid=mat["uid"], name=mat["name"])
                # Journal the id the moment CAMDS issues it: a failure while the
                # node is being filled must still leave the id recoverable.
                ref = await self.io.create_root(mat, on_allocated=lambda allocated: record(
                    "material_id_allocated", uid=mat["uid"], ref=allocated))
                refs[mat["uid"]] = ref
                await save(mat["uid"])
                reporter.done()
                substance_names = {}
                # Rest is attached last so other portions are already present.
                for substance in sorted(mat["children"], key=lambda n: bool(n.get("is_rest"))):
                    await gate()
                    reporter.step(substance["uid"], substance["name"], "SUBSTANCE", (mat["name"],))
                    record("add_substance_requested", uid=substance["uid"], name=substance["name"])
                    substance_names[substance["uid"]] = await self.io.add_substance(mat["name"], substance)
                    await save(substance["uid"])
                    reporter.done()
                await self.set_applications(mat, record, gate, reporter)
                reporter.step(mat["uid"], mat["name"], "MATERIAL")
                reporter("verify_started")
                await self.io.open_saved("Material", ref)
                await self.io.verify_value("Material Name", mat["name"])
                await self.io.verify_value("Material No.", mat.get("material_number") or "")
                await self.io.verify_child_count([mat["name"]], len(mat["children"]))
                for substance in mat["children"]:
                    await self.io.verify_substance([mat["name"], substance_names[substance["uid"]]], substance)
                names[mat["uid"]] = mat["name"]
                record("material_readback_verified", uid=mat["uid"], ref=ref, display_name=mat["name"])
            root = request.root
            if root["node_type"] == "MATERIAL":
                root_ref = refs[root["uid"]]
            else:
                # Repeated names are addressed by document order instead of being
                # refused: CAMDS shows children in the order they were added, and
                # the final read-back checks every value in that same order.
                def label(node):
                    return names[node["uid"]] if node["node_type"] == "MATERIAL" else node["name"]
                seen, place = {}, {}
                def enumerate_paths(node, prefix):
                    path = prefix + (label(node),)
                    place[node["uid"]] = (path, seen.get(path, 0))
                    seen[path] = seen.get(path, 0) + 1
                    for child in node.get("children", []):
                        enumerate_paths(child, path)
                enumerate_paths(root, ())
                def at(uid):
                    path, occurrence = place[uid]
                    return occurrence, seen[path]
                await gate()
                reporter.step(root["uid"], root["name"], "COMPONENT")
                record("create_parent_requested", uid=root["uid"], name=root["name"])
                root_ref = await self.io.create_root(root, on_allocated=lambda allocated: record(
                    "parent_id_allocated", uid=root["uid"], ref=allocated))
                await save(root["uid"])
                reporter.done()

                async def build(node, path):
                    for child in node["children"]:
                        await gate()
                        if child["node_type"] in ("COMPONENT", "SEMICOMPONENT"):
                            semi = child["node_type"] == "SEMICOMPONENT"
                            reporter.step(child["uid"], child["name"], child["node_type"], path)
                            record("add_semicomponent_requested" if semi else "add_child_requested",
                                   uid=child["uid"], name=child["name"])
                            adder = self.io.add_semicomponent if semi else self.io.add_component
                            await adder(path, child, at=at(node["uid"]))
                            await save(child["uid"])
                            reporter.done()
                            await build(child, path + [child["name"]])
                        else:
                            reporter.step(child["uid"], child["name"], "MATERIAL", path)
                            record("attach_material_requested", uid=child["uid"], ref=refs[child["uid"]])
                            await self.io.add_material(path, child, refs[child["uid"]], at=at(node["uid"]),
                                                       by_portion=node["node_type"] == "SEMICOMPONENT")
                            await save(child["uid"])
                            reporter.done()
                await build(root, [root["name"]])
                reporter.step(root["uid"], root["name"], "COMPONENT")
                reporter("verify_started")
                await self.io.open_saved("Component", root_ref)

                async def verify(node, path):
                    await self.io.select(path, at=at(node["uid"]))
                    await self.io.verify_child_count(path, len(node["children"]), at=at(node["uid"]))
                    await self.io.verify_value("Article Name", node["name"])
                    if node["node_type"] == "SEMICOMPONENT":
                        # A Semicomponent carries a Mass and no Quantity.
                        await self.io.verify_value("Semicomponent No.", node.get("part_number") or "")
                        await self.io.verify_value("Mass", float(node["weight_g"]))
                    else:
                        await self.io.verify_value("Component No.", node.get("part_number") or "")
                        await self.io.verify_value("Measured Mass per Item", float(node["weight_g"]))
                        if len(path) > 1:
                            await self.io.verify_value("Quantity", float(node["quantity"]))
                    for child in node["children"]:
                        if child["node_type"] in ("COMPONENT", "SEMICOMPONENT"):
                            await verify(child, path + [child["name"]])
                        else:
                            await self.io.select(path + [names[child["uid"]]], at=at(child["uid"]))
                            if await self.io.identity() != tuple(refs[child["uid"]]):
                                raise RuntimeError("Saved Material reference mismatch")
                            if node["node_type"] == "SEMICOMPONENT":
                                await self.io.verify_proportion(child, "Material")
                            else:
                                await self.io.verify_value("Mass", float(child["weight_g"]))
                await verify(root, [root["name"]])
            record("complete_readback_verified", root_ref=root_ref, material_refs=refs)
            # Differences the backend accepted during read-back are the
            # operator's to judge, so they travel back with the result.
            found = list(await self.io.read_back_findings())
            if found:
                journal.record("readback_findings", findings=found)
            return {"kind": "import_tree", "identity": "/".join(root_ref), "editor_open": False,
                    "nodes": reporter.completed, "total": reporter.total,
                    "warnings": warnings + found, "merges": request.merges,
                    "skipped": list(self.skipped),
                    "note": "Draft tree saved and verified through Search / View. No Send/Submit. Journal: " + str(journal.path)}
        except BaseException as exc:
            stopped = isinstance(exc, ImportStopped)
            reporter.fail()
            reporter("stopped" if stopped else "node_failed", name=str(exc))
            journal.record("stopped_by_operator" if stopped else "interrupted_or_failed",
                           error=str(exc), material_refs=refs, completed=sorted(done_uids | set(names)))
            raise
