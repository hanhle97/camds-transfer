"""Semicomponent support, per SEMICOMPONENT_APPLICATION_DISCOVERY.md.

Inserting the Semicomponent itself is verified: no reference dialog, a Mass and
a Semicomponent No., no Quantity.

A Semicomponent inside another one is declared by portion, not by mass - the
report gives "Contact Bimetal | 20291057 | Rest 98.74" and no weight at all.
The API writes that portion; the browser backend has no observed control for
it and refuses.
"""
import pytest
from playwright.async_api import async_playwright

from camds_imds_importer.camds.import_plan import ImportRequest
from camds_imds_importer.camds.operations import CamdsOperations
from camds_imds_importer.camds.tree_import import DraftBrowser


def substance(uid="s"):
    return {"uid": uid, "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6",
            "percentage": 100, "children": []}


def material(uid="m", mass=1.0, name="Steel"):
    return {"uid": uid, "node_type": "MATERIAL", "name": name, "classification": "1.1.1",
            "weight_g": mass, "children": [substance("s" + uid)]}


def semicomponent(children, uid="sc", mass=1.0, name="White PP film"):
    return {"uid": uid, "node_type": "SEMICOMPONENT", "name": name, "part_number": "PP539-LN",
            "weight_g": mass, "children": children}


def tree(children):
    return {"uid": "r", "node_type": "COMPONENT", "name": "Label", "weight_g": 1.0,
            "children": [semicomponent(children)]}


def test_a_component_may_contain_a_semicomponent():
    ImportRequest(tree([dict(material(), weight_g=None, percentage=100.0)])).validate()


def _nested(**portion):
    inner = semicomponent([dict(material(), weight_g=None, percentage=100.0)],
                          uid="inner", name="Inner film")
    inner["weight_g"] = None
    inner.update(portion)
    return tree([inner])


def test_a_semicomponent_inside_a_semicomponent_is_declared_by_portion():
    """"Rest 98.74" and no mass: the report declares it the way a Material there
    is declared, so a mass is not what is missing."""
    ImportRequest(_nested(is_rest=True, percentage=98.74)).validate()
    ImportRequest(_nested(percentage=1.26)).validate()


def test_a_nested_semicomponent_without_a_portion_is_still_refused():
    problems = str(pytest.raises(ValueError, ImportRequest(_nested()).validate).value)
    assert "Fixed, Range, Rest" in problems
    assert "mass" not in problems, "a nested Semicomponent is never asked for a mass"


def test_a_semicomponent_needs_a_mass_but_never_a_quantity():
    root = tree([dict(material(), weight_g=None, percentage=100.0)])
    root["children"][0]["weight_g"] = None
    problems = str(pytest.raises(ValueError, ImportRequest(root).validate).value)
    assert "mass" in problems
    assert "quantity" not in problems.lower(), "CAMDS shows no Quantity for a Semicomponent"


def test_a_semicomponent_may_not_hold_a_component():
    part = {"uid": "c", "node_type": "COMPONENT", "name": "Part", "weight_g": 1.0, "quantity": 1,
            "children": [material()]}
    with pytest.raises(ValueError, match="a SEMICOMPONENT accepts only"):
        ImportRequest(tree([part])).validate()


def test_material_inside_a_semicomponent_is_declared_by_portion_not_mass():
    # CAMDS asks a Material under a Semicomponent for a Proportion. A mass alone
    # is not enough, and a portion alone is.
    with pytest.raises(ValueError, match="specify exactly one of Fixed, Range, Rest"):
        ImportRequest(tree([material()])).validate()
    by_portion = dict(material(), weight_g=None, percentage=100.0)
    ImportRequest(tree([by_portion])).validate()


def test_semicomponent_contents_that_do_not_close_are_reported_not_refused():
    root = tree([dict(material("m1", name="Steel"), weight_g=None, percentage=60.0),
                 dict(material("m2", name="Copper"), weight_g=None, percentage=30.0)])
    warnings = ImportRequest(root).validate()
    assert any("contents total 90-90%" in w for w in warnings)


def test_a_semicomponent_can_declare_rest_only_once():
    root = tree([dict(material("m1", name="Steel"), weight_g=None, is_rest=True),
                 dict(material("m2", name="Copper"), weight_g=None, is_rest=True)])
    with pytest.raises(ValueError, match="Semicomponent can declare Rest only once"):
        ImportRequest(root).validate()


def test_material_under_a_component_still_needs_its_mass():
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Holder", "weight_g": 1.0,
            "children": [material()]}
    ImportRequest(root).validate()
    root["children"][0]["weight_g"] = None
    with pytest.raises(ValueError, match="reference mass"):
        ImportRequest(root).validate()


# The editor fixture mirrors the recorded form: Add SemiComponent inserts the
# node directly, Type must read Semicomponent, and Mass carries a unit button.
EDITOR = """
<img title="Add SemiComponent" alt="添加半成品部件" onclick="insert()">
<div role="tabpanel" aria-label="Details">
  <div class="el-form-item"><label class="el-form-item__label">Type</label><span id="type">Component</span></div>
  <div class="el-form-item"><label class="el-form-item__label">Article Name</label><input role="textbox"></div>
  <div class="el-form-item"><label class="el-form-item__label">Semicomponent No.</label><input role="textbox"></div>
  <div class="el-form-item"><label class="el-form-item__label">Mass</label>
      <input role="textbox" value="0"><button id="unit">g</button></div>
</div>
<script>function insert() { document.getElementById('type').innerText = 'Semicomponent'; }</script>
"""


@pytest.fixture
async def browser_page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.set_default_timeout(3000)
        yield page
        await browser.close()


async def test_add_semicomponent_fills_the_recorded_fields(browser_page):
    await browser_page.set_content(EDITOR)
    draft = DraftBrowser(CamdsOperations(browser_page))
    draft.select = lambda path, at=(0, 1): _noop()
    draft.settled = _noop
    await draft.add_semicomponent(["Label"], semicomponent([], mass=0.995))
    panel = browser_page.get_by_role("tabpanel", name="Details", exact=True)
    values = await panel.locator("input").evaluate_all("els => els.map(e => e.value)")
    assert values == ["White PP film", "PP539-LN", "0.995"]
    assert await browser_page.locator("#type").inner_text() == "Semicomponent"


async def test_add_semicomponent_stops_if_the_mass_unit_is_not_grams(browser_page):
    await browser_page.set_content(EDITOR.replace('<button id="unit">g</button>',
                                                  '<button id="unit">kg</button>'))
    draft = DraftBrowser(CamdsOperations(browser_page))
    draft.select = lambda path, at=(0, 1): _noop()
    draft.settled = _noop
    with pytest.raises(Exception):
        await draft.add_semicomponent(["Label"], semicomponent([], mass=0.995))


async def _noop(*args, **kwargs):
    return None


PORTION_EDITOR = """
<div role="tabpanel" aria-label="Details">
  <div class="el-form-item"><label class="el-form-item__label">Proportion</label>
    <label role="radio" class="el-radio" aria-checked="false"><input value="1">
        <input type="text"><input type="text"></label>
    <label role="radio" class="el-radio" aria-checked="false"><input value="2">
        <input type="text"></label>
    <label role="radio" class="el-radio" aria-checked="false"><input value="3"></label>
  </div>
</div>
<script>
document.querySelectorAll('label[role=radio]').forEach(l => l.addEventListener('click', () => {
  document.querySelectorAll('label[role=radio]').forEach(o => o.setAttribute('aria-checked', 'false'));
  l.setAttribute('aria-checked', 'true');
}));
</script>
"""


@pytest.mark.parametrize("node, radio, values", [
    ({"name": "Steel", "percentage": 42.5}, "2", ["42.5"]),
    ({"name": "Steel", "percentage_min": 1.0, "percentage_max": 3.0}, "1", ["1", "3"]),
    ({"name": "Steel", "is_rest": True}, "3", []),
])
async def test_material_portion_uses_the_same_widget_as_a_substance(browser_page, node, radio, values):
    await browser_page.set_content(PORTION_EDITOR)
    draft = DraftBrowser(CamdsOperations(browser_page))
    await draft.set_proportion(node)
    chosen = browser_page.locator(f'label[role="radio"]:has(input[value="{radio}"])')
    assert await chosen.get_attribute("aria-checked") == "true"
    assert await chosen.locator('input[type="text"]').evaluate_all("els => els.map(e => e.value)") == values


class SemiRecorder:
    """Fake editor covering the whole Semicomponent relation, including read-back."""

    def __init__(self):
        self.calls = []
        self.portions = []
        self.verified = {}
        self.reporter = None
        self.ref = None

    async def prepare(self):
        pass

    async def create_root(self, node, on_allocated=None):
        self.ref = (("CA_8_" if node["node_type"] == "MATERIAL" else "CA_5_") + node["uid"], "0.01")
        if on_allocated:
            on_allocated(self.ref)
        return self.ref

    async def save(self):
        self.calls.append(("save", None))

    async def add_substance(self, name, node):
        return node["name"]

    async def open_saved(self, kind, ref):
        self.ref = ref

    async def value(self, label):
        return "Steel"

    async def identity(self):
        return self.ref

    async def verify_value(self, label, expected):
        self.verified[label] = expected

    async def verify_child_count(self, path, count, at=(0, 1)):
        pass

    async def verify_substance(self, path, node, at=(0, 1)):
        pass

    async def can_reenter_saved(self):
        return False

    async def saved_children(self, path, at=(0, 1)):
        return []

    async def read_back_findings(self):
        return []

    async def verify_proportion(self, node, what="Substance"):
        self.portions.append(("verify", what, node["name"]))

    async def select(self, path, at=(0, 1)):
        if path[-1] == "Steel":
            self.ref = ("CA_8_m", "0.01")

    async def add_component(self, path, node, at=(0, 1), reuse_index=None):
        self.calls.append(("component", node["uid"]))

    async def add_semicomponent(self, path, node, at=(0, 1), by_portion=False, reuse_index=None):
        self.calls.append(("semicomponent", node["uid"], by_portion))

    async def add_material(self, path, node, ref, at=(0, 1), by_portion=False, reuse_index=None):
        self.calls.append(("material", node["uid"]))
        self.portions.append(("add", "portion" if by_portion else "mass", node["name"]))
        self.ref = ref
        return node["name"]


async def test_the_whole_semicomponent_relation_runs_and_reads_back(tmp_path):
    from camds_imds_importer.camds.tree_import import TreeImporter
    root = tree([dict(material(), weight_g=None, percentage=100.0)])
    backend = SemiRecorder()
    await TreeImporter(backend, tmp_path).run(ImportRequest(root))
    assert [c for c in backend.calls if c[0] != "save"] == [("semicomponent", "sc", False),
                                                           ("material", "m")]
    # Written as a portion, and read back as a portion, never as a mass.
    assert backend.portions == [("add", "portion", "Steel"), ("verify", "Material", "Steel")]
    assert backend.verified["Semicomponent No."] == "PP539-LN"
    assert backend.verified["Mass"] == 1.0, "the Semicomponent itself still carries a Mass"


async def test_a_material_under_a_component_is_still_written_as_a_mass(tmp_path):
    from camds_imds_importer.camds.tree_import import TreeImporter
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Holder", "weight_g": 1.0,
            "children": [material()]}
    backend = SemiRecorder()
    await TreeImporter(backend, tmp_path).run(ImportRequest(root))
    assert backend.portions == [("add", "mass", "Steel")]
    assert backend.verified["Mass"] == 1.0


async def test_a_nested_semicomponent_is_written_and_read_back_as_a_portion(tmp_path):
    """The whole nested relation: no mass is written, and none is verified."""
    from camds_imds_importer.camds.tree_import import TreeImporter

    backend = SemiRecorder()
    await TreeImporter(backend, tmp_path).run(
        ImportRequest(_nested(is_rest=True, percentage=98.74)))

    assert ("semicomponent", "inner", True) in backend.calls
    assert ("semicomponent", "sc", False) in backend.calls, "the outer one still carries a mass"
    assert ("verify", "Semicomponent", "Inner film") in backend.portions
    assert backend.verified["Mass"] == 1.0, "only the outer Semicomponent's mass"


async def test_the_browser_backend_refuses_a_nested_semicomponent():
    """No control for a nested portion has been observed, so it fails closed."""
    from camds_imds_importer.camds.tree_import import DraftBrowser

    browser = DraftBrowser.__new__(DraftBrowser)
    with pytest.raises(RuntimeError, match="no browser control"):
        await browser.add_semicomponent(["Label"], {"name": "Inner film"}, by_portion=True)
