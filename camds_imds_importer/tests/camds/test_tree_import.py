import copy
import json

import pytest

from camds_imds_importer.camds.import_plan import ImportRequest, proportion
from camds_imds_importer.camds.tree_import import TreeImporter


def fixture_tree():
    substance = {"uid": "s", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6", "percentage": 100, "children": []}
    material = {"uid": "m", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1", "weight_g": 5, "material_number": "M1", "children": [substance]}
    child = {"uid": "c", "node_type": "COMPONENT", "name": "Child", "weight_g": 5, "quantity": 2, "children": [material]}
    return {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 10, "children": [child]}


def test_mass_quantity_and_composition_preflight():
    root = fixture_tree()
    ImportRequest(root).validate()
    root["children"][0]["quantity"] = 1
    with pytest.raises(ValueError, match="children total"):
        ImportRequest(root).validate()
    root["children"][0]["quantity"] = None
    with pytest.raises(ValueError, match="quantity"):
        ImportRequest(root).validate()


def test_mapping_existing_material_does_not_confuse_imds_id_with_camds_id():
    root = fixture_tree()
    mat = root["children"][0]["children"][0]
    mat["classification"] = "5.1.a"
    mat["imds_id"] = "123456"
    with pytest.raises(ValueError, match="map an existing"):
        ImportRequest(root).validate()
    with pytest.raises(ValueError, match="exact CAMDS"):
        ImportRequest(root, {"m": ("123456", "1")}).validate()
    ImportRequest(root, {"m": ("CA_8_123456", "1")}).validate()


@pytest.mark.parametrize("changes", [
    {"percentage": 90}, {"percentage": float('nan')},
    {"percentage": 100, "is_rest": True},
    {"percentage": None, "percentage_min": 20, "percentage_max": 10},
    {"cas_number": "system"},
])
def test_invalid_substance_blocks_full_tree_before_creation(changes):
    root = fixture_tree()
    root["children"][0]["children"][0]["children"][0].update(changes)
    with pytest.raises(ValueError):
        ImportRequest(root).validate()


def test_snapshot_is_detached_from_parser_edits():
    root = fixture_tree()
    request = ImportRequest(root).snapshot()
    key = request.fingerprint
    root["name"] = "Changed in parser"
    assert request.root["name"] == "Parent"
    assert request.fingerprint == key


class FakeDraftBrowser:
    def __init__(self, fail_save=None, fail_readback=False):
        self.calls = []
        self.saves = 0
        self.fail_save = fail_save
        self.fail_readback = fail_readback
        self.current_ref = None

    async def prepare(self):
        pass

    async def create_root(self, node):
        self.calls.append(("create", node["uid"]))
        self.current_ref = ("CA_8_100" if node["node_type"] == "MATERIAL" else "CA_5_200", "0.01")
        return self.current_ref

    async def save(self):
        self.saves += 1
        self.calls.append(("save", self.saves))
        if self.saves == self.fail_save:
            raise RuntimeError("Save response lost")

    async def add_substance(self, name, node):
        self.calls.append(("substance", node["uid"]))
        return node["name"]

    async def open_saved(self, kind, ref):
        self.calls.append(("view", kind))
        self.current_ref = ref

    async def value(self, label):
        return "Steel"

    async def verify_value(self, label, expected):
        self.calls.append(("verify", label))
        if self.fail_readback:
            raise RuntimeError("Read-back mismatch")

    async def verify_child_count(self, path, count):
        self.calls.append(("verify_count", count))

    async def verify_substance(self, path, node):
        self.calls.append(("verify_cas", node["cas_number"]))

    async def select(self, path):
        self.calls.append(("select", path[-1]))
        if path[-1] == "Steel":
            self.current_ref = ("CA_8_100", "0.01")

    async def add_component(self, path, node):
        self.calls.append(("child", node["uid"]))

    async def add_material(self, path, node, ref):
        self.calls.append(("reference", node["uid"]))
        self.current_ref = ref
        return "Steel"

    async def identity(self):
        return self.current_ref


async def test_verified_flow_saves_root_before_children_and_every_change(tmp_path):
    backend = FakeDraftBrowser()
    result = await TreeImporter(backend, tmp_path).run(ImportRequest(fixture_tree()))
    mutations = [call for call in backend.calls if call[0] in {"create", "save", "substance", "child", "reference"}]
    assert mutations == [("create", "m"), ("save", 1), ("substance", "s"), ("save", 2),
                         ("create", "r"), ("save", 3), ("child", "c"), ("save", 4),
                         ("reference", "m"), ("save", 5)]
    assert ("verify_cas", "7439-89-6") in backend.calls
    assert result["identity"] == "CA_5_200/0.01"
    events = [json.loads(line) for line in next(tmp_path.glob('*.jsonl')).read_text().splitlines()]
    assert events[-1]["event"] == "complete_readback_verified"


async def test_error_preserves_journal_and_replay_does_not_create_duplicates(tmp_path):
    backend = FakeDraftBrowser(fail_save=1)
    request = ImportRequest(fixture_tree())
    with pytest.raises(RuntimeError, match="response lost"):
        await TreeImporter(backend, tmp_path).run(request)
    events = [json.loads(line) for line in next(tmp_path.glob('*.jsonl')).read_text().splitlines()]
    assert events[-1]["event"] == "interrupted_or_failed"
    assert events[-1]["material_refs"]["m"] == ["CA_8_100", "0.01"]
    calls_before = list(backend.calls)
    with pytest.raises(RuntimeError, match="automatic replay is blocked"):
        await TreeImporter(backend, tmp_path).run(request)
    assert backend.calls == calls_before


async def test_readback_failure_never_reports_complete(tmp_path):
    with pytest.raises(RuntimeError, match="mismatch"):
        await TreeImporter(FakeDraftBrowser(fail_readback=True), tmp_path).run(ImportRequest(fixture_tree()))
    text = next(tmp_path.glob('*.jsonl')).read_text()
    assert 'complete_readback_verified' not in text


async def test_existing_material_is_reused_without_creating_substances(tmp_path):
    backend = FakeDraftBrowser()
    request = ImportRequest(fixture_tree(), {"m": ("CA_8_100", "0.01")})
    await TreeImporter(backend, tmp_path).run(request)
    assert ("create", "m") not in backend.calls
    assert ("substance", "s") not in backend.calls


async def test_preflight_failure_has_no_journal_or_browser_side_effects(tmp_path):
    root = fixture_tree()
    root["children"][0]["node_type"] = "SEMICOMPONENT"
    backend = FakeDraftBrowser()
    with pytest.raises(ValueError):
        await TreeImporter(backend, tmp_path).run(ImportRequest(root))
    assert not backend.calls
    assert not list(tmp_path.iterdir())
