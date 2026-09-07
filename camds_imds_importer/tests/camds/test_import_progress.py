"""Progress counters, Pause/Stop and journal-based resume for the recursive import."""
import asyncio
import json

import pytest

from camds_imds_importer.camds.import_control import ImportControl, ImportStopped
from camds_imds_importer.camds.import_plan import ImportRequest
from camds_imds_importer.camds.tree_import import TreeImporter, read_journal


def fixture_tree():
    substance = {"uid": "s", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6", "percentage": 100, "children": []}
    material = {"uid": "m", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1", "weight_g": 5, "material_number": "M1", "children": [substance]}
    child = {"uid": "c", "node_type": "COMPONENT", "name": "Child", "weight_g": 5, "quantity": 2, "children": [material]}
    return {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 10, "children": [child]}


def deeper_tree():
    def substance(uid, cas):
        return {"uid": uid, "node_type": "SUBSTANCE", "name": "S" + uid, "cas_number": cas, "percentage": 100, "children": []}
    m1 = {"uid": "m1", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1", "weight_g": 4, "children": [substance("s1", "7439-89-6")]}
    m2 = {"uid": "m2", "node_type": "MATERIAL", "name": "Copper", "classification": "1.1.1", "weight_g": 2, "children": [substance("s2", "7440-50-8")]}
    inner = {"uid": "c2", "node_type": "COMPONENT", "name": "Inner", "weight_g": 2, "quantity": 1, "children": [m2]}
    outer = {"uid": "c1", "node_type": "COMPONENT", "name": "Outer", "weight_g": 6, "quantity": 1, "children": [m1, inner]}
    return {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 6, "children": [outer]}


class FakeDraftBrowser:

    async def can_reenter_saved(self):
        return False

    async def saved_children(self, path, at=(0, 1)):
        return []

    async def find_existing_material(self, node):
        return None

    async def read_back_findings(self):
        return []
    def __init__(self, on_save=None, fail_create=None):
        self.calls = []
        self.saves = 0
        self.on_save = on_save
        self.fail_create = fail_create
        self.current_ref = None
        self.reporter = None
        self.by_name = {}

    async def prepare(self):
        pass

    async def create_root(self, node, on_allocated=None):
        if self.fail_create == node["uid"]:
            raise RuntimeError("Create lost for " + node["uid"])
        self.calls.append(("create", node["uid"]))
        self.current_ref = (("CA_8_" if node["node_type"] == "MATERIAL" else "CA_5_") + node["uid"], "0.01")
        self.by_name[node["name"]] = self.current_ref
        if on_allocated:
            on_allocated(self.current_ref)
        return self.current_ref

    async def save(self):
        self.saves += 1
        self.calls.append(("save", self.saves))
        if self.on_save:
            self.on_save(self.saves)

    async def add_substance(self, name, node):
        self.calls.append(("substance", node["uid"]))
        return node["name"]

    async def open_saved(self, kind, ref):
        self.calls.append(("view", kind))
        self.current_ref = ref

    async def value(self, label):
        return "Steel"

    async def verify_value(self, label, expected):
        pass

    async def verify_child_count(self, path, count, at=(0, 1)):
        pass

    async def verify_substance(self, path, node, at=(0, 1)):
        pass

    async def select(self, path, at=(0, 1)):
        self.current_ref = self.by_name.get(path[-1], self.current_ref)

    async def add_semicomponent(self, path, node, at=(0, 1)):
        self.calls.append(("semi", node["uid"])) if hasattr(self, "calls") else None
        if hasattr(self, "addressed"):
            self.addressed.append(("semi", tuple(path), at))

    async def add_component(self, path, node, at=(0, 1), reuse_index=None):
        self.calls.append(("child", node["uid"]))

    async def add_material(self, path, node, ref, at=(0, 1), by_portion=False, reuse_index=None):
        self.calls.append(("reference", node["uid"]))
        self.current_ref = ref
        self.by_name[node["name"]] = tuple(ref)
        return node["name"]

    async def identity(self):
        return self.current_ref


@pytest.mark.parametrize("tree", [fixture_tree(), deeper_tree()])
async def test_plan_total_matches_the_steps_actually_executed(tmp_path, tree):
    events = []
    request = ImportRequest(tree)
    await TreeImporter(FakeDraftBrowser(), tmp_path, progress=events.append).run(request)
    final = events[-1]
    assert final.event == "complete_readback_verified"
    # A counter that drifts from the plan would show a wrong "12 / 148" to the operator.
    assert final.completed == final.total == len(request.plan())
    assert final.failed == 0


async def test_progress_reports_kind_parent_path_and_counter(tmp_path):
    events = []
    request = ImportRequest(deeper_tree())
    await TreeImporter(FakeDraftBrowser(), tmp_path, progress=events.append).run(request)
    attach = next(e for e in events if e.event == "attach_material_requested" and e.name == "Copper")
    assert attach.kind == "MATERIAL"
    assert attach.parent_path == "Parent / Outer / Inner"
    assert attach.counter.endswith(f"/ {len(request.plan())}")
    substance = next(e for e in events if e.event == "add_substance_requested")
    assert substance.kind == "SUBSTANCE" and substance.path == ("Steel",)


async def test_stop_between_steps_keeps_saved_objects_and_records_the_reason(tmp_path):
    control = ImportControl()
    backend = FakeDraftBrowser(on_save=lambda count: control.stop() if count == 2 else None)
    with pytest.raises(ImportStopped):
        await TreeImporter(backend, tmp_path, control=control).run(ImportRequest(fixture_tree()))
    # Stop is honoured only at a boundary. The Material in flight finishes its
    # read-only verification, so the journal can record it as complete and the
    # parent Component is never started.
    assert backend.calls == [("create", "m"), ("save", 1), ("substance", "s"), ("save", 2), ("view", "Material")]
    events = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text().splitlines()]
    assert events[-1]["event"] == "stopped_by_operator"
    assert events[-1]["material_refs"]["m"] == ["CA_8_m", "0.01"]
    # A stop must leave a state that resume can pick up without duplicating IDs.
    state = read_journal(next(tmp_path.glob("*.jsonl")))
    assert state.completed == {"m"} and not state.incomplete_materials and state.root_ref is None


async def test_pause_halts_at_a_boundary_until_resumed(tmp_path):
    control = ImportControl()
    backend = FakeDraftBrowser(on_save=lambda count: control.pause() if count == 1 else None)
    events = []
    task = asyncio.create_task(TreeImporter(backend, tmp_path, progress=events.append, control=control).run(ImportRequest(fixture_tree())))
    for _ in range(40):
        await asyncio.sleep(0.05)
        if any(e.event == "paused" for e in events):
            break
    assert any(e.event == "paused" for e in events), "import did not stop at the step boundary"
    assert ("substance", "s") not in backend.calls
    control.resume()
    result = await task
    assert result["nodes"] == result["total"]
    assert ("substance", "s") in backend.calls


async def test_resume_skips_verified_materials_without_recreating_them(tmp_path):
    request = ImportRequest(fixture_tree())
    first = FakeDraftBrowser(fail_create="r")
    with pytest.raises(RuntimeError, match="Create lost"):
        await TreeImporter(first, tmp_path).run(request)
    state = read_journal(next(tmp_path.glob("*.jsonl")))
    assert state.completed == {"m"} and state.material_refs["m"] == ("CA_8_m", "0.01")

    second = FakeDraftBrowser()
    events = []
    result = await TreeImporter(second, tmp_path, progress=events.append).run(request, resume=True)
    assert ("create", "m") not in second.calls
    assert ("substance", "s") not in second.calls
    assert ("reference", "m") in second.calls
    assert any(e.event == "skipped_completed" for e in events)
    assert result["identity"] == "CA_5_r/0.01"


async def test_resume_refuses_when_a_material_was_created_but_never_verified(tmp_path):
    request = ImportRequest(fixture_tree())
    backend = FakeDraftBrowser(on_save=lambda count: (_ for _ in ()).throw(RuntimeError("Save lost")) if count == 2 else None)
    with pytest.raises(RuntimeError, match="Save lost"):
        await TreeImporter(backend, tmp_path).run(request)
    retry = FakeDraftBrowser()
    with pytest.raises(RuntimeError, match="created but never verified"):
        await TreeImporter(retry, tmp_path).run(request, resume=True)
    assert not retry.calls


async def test_resume_refuses_after_the_parent_component_exists(tmp_path):
    request = ImportRequest(fixture_tree())
    backend = FakeDraftBrowser(on_save=lambda count: (_ for _ in ()).throw(RuntimeError("Save lost")) if count == 4 else None)
    with pytest.raises(RuntimeError, match="Save lost"):
        await TreeImporter(backend, tmp_path).run(request)
    retry = FakeDraftBrowser()
    with pytest.raises(RuntimeError, match="parent Component CA_5_r/0.01 was already created"):
        await TreeImporter(retry, tmp_path).run(request, resume=True)
    assert not retry.calls


async def test_completed_import_is_never_resumed_into_duplicates(tmp_path):
    request = ImportRequest(fixture_tree())
    await TreeImporter(FakeDraftBrowser(), tmp_path).run(request)
    retry = FakeDraftBrowser()
    with pytest.raises(RuntimeError, match="already completed"):
        await TreeImporter(retry, tmp_path).run(request, resume=True)
    with pytest.raises(RuntimeError, match="automatic replay is blocked"):
        await TreeImporter(retry, tmp_path).run(request)
    assert not retry.calls


def test_journal_reader_ignores_a_truncated_final_line(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text(
        json.dumps({"event": "material_readback_verified", "uid": "m", "ref": ["CA_8_1", "0.01"], "display_name": "Steel"})
        + chr(10) + '{"event": "material_id_all',
        encoding="utf-8")
    state = read_journal(path)
    assert state.completed == {"m"} and state.names["m"] == "Steel" and not state.incomplete_materials
