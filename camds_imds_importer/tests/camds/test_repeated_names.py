"""A board really does carry 30 parts named "Resistor"; that must import."""
import pytest

from camds_imds_importer.camds.import_plan import ImportRequest
from camds_imds_importer.camds.tree_import import TreeImporter


def resistor(uid, mass=1.0):
    substance = {"uid": "s" + uid, "node_type": "SUBSTANCE", "name": "Iron",
                 "cas_number": "7439-89-6", "percentage": 100, "children": []}
    material = {"uid": "m" + uid, "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1",
                "weight_g": mass, "children": [substance]}
    return {"uid": uid, "node_type": "COMPONENT", "name": "Resistor", "weight_g": mass,
            "quantity": 1, "children": [material]}


def board(count=3):
    kids = [resistor(f"r{i}") for i in range(count)]
    return {"uid": "root", "node_type": "COMPONENT", "name": "Board",
            "weight_g": float(count), "children": kids}


class RecordingBrowser:
    """Records the (path, occurrence, total) each call addressed."""

    def __init__(self):
        self.addressed = []
        self.reporter = None
        self.ref = None
        # Materials are created in tree order, so the nth "Steel" on a path is
        # the nth Material created; that is exactly what occurrence addresses.
        self.materials = []

    async def prepare(self):
        pass

    async def create_root(self, node, on_allocated=None):
        self.ref = (("CA_8_" if node["node_type"] == "MATERIAL" else "CA_5_") + node["uid"], "0.01")
        if node["node_type"] == "MATERIAL":
            self.materials.append(self.ref)
        if on_allocated:
            on_allocated(self.ref)
        return self.ref

    async def save(self):
        pass

    async def add_substance(self, name, node):
        return node["name"]

    async def read_back_findings(self):
        return []

    async def open_saved(self, kind, ref):
        self.ref = ref

    async def value(self, label):
        return "Steel"

    async def verify_value(self, label, expected):
        pass

    async def identity(self):
        return self.ref

    async def select(self, path, at=(0, 1)):
        self.addressed.append(("select", tuple(path), at))
        if path[-1] == "Steel":
            self.ref = self.materials[at[0]]

    async def verify_child_count(self, path, count, at=(0, 1)):
        self.addressed.append(("count", tuple(path), at))

    async def verify_substance(self, path, node, at=(0, 1)):
        pass

    async def add_semicomponent(self, path, node, at=(0, 1)):
        self.calls.append(("semi", node["uid"])) if hasattr(self, "calls") else None
        if hasattr(self, "addressed"):
            self.addressed.append(("semi", tuple(path), at))

    async def add_component(self, path, node, at=(0, 1)):
        self.addressed.append(("add", tuple(path), at))

    async def add_material(self, path, node, ref, at=(0, 1), by_portion=False):
        self.addressed.append(("material", tuple(path), at))
        self.ref = ref
        return "Steel"


def test_preflight_accepts_repeated_sibling_names():
    ImportRequest(board(30)).validate()


def test_parent_and_child_may_share_a_name():
    inner = {"uid": "i", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1", "weight_g": 1.0,
             "children": [{"uid": "s", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6",
                           "percentage": 100, "children": []}]}
    child = {"uid": "c", "node_type": "COMPONENT", "name": "SCREW", "weight_g": 1.0, "quantity": 1,
             "children": [inner]}
    root = {"uid": "r", "node_type": "COMPONENT", "name": "SCREW", "weight_g": 1.0, "children": [child]}
    ImportRequest(root).validate()


async def test_each_repeated_sibling_is_addressed_by_its_own_position(tmp_path):
    browser = RecordingBrowser()
    await TreeImporter(browser, tmp_path).run(ImportRequest(board(3)))
    # Every "Resistor" under "Board" shares one path, so the occurrence is what
    # tells them apart, and the total must match how many CAMDS will show.
    resistor_selects = [a for a in browser.addressed
                        if a[0] == "select" and a[1] == ("Board", "Resistor")]
    assert [a[2] for a in resistor_selects] == [(0, 3), (1, 3), (2, 3)]


async def test_a_unique_path_is_still_addressed_as_a_single_match(tmp_path):
    browser = RecordingBrowser()
    root = board(1)
    root["children"][0]["name"] = "Only child"
    await TreeImporter(browser, tmp_path).run(ImportRequest(root))
    unique = [a for a in browser.addressed if a[1] == ("Board", "Only child")]
    assert unique and all(at == (0, 1) for _, _, at in unique)


async def test_materials_repeated_under_one_parent_keep_distinct_positions(tmp_path):
    # Two identical Materials attached to the same Component both show as "Steel".
    def material(uid):
        return {"uid": uid, "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1",
                "weight_g": 1.0, "children": [{"uid": "s" + uid, "node_type": "SUBSTANCE", "name": "Iron",
                                               "cas_number": "7439-89-6", "percentage": 100, "children": []}]}
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Holder", "weight_g": 2.0,
            "children": [material("m1"), material("m2")]}
    browser = RecordingBrowser()
    await TreeImporter(browser, tmp_path).run(ImportRequest(root))
    steel = [a[2] for a in browser.addressed if a[0] == "select" and a[1] == ("Holder", "Steel")]
    assert steel == [(0, 2), (1, 2)]
