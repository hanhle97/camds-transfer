import copy
import json

import pytest

from camds_imds_importer.camds.import_plan import ImportRequest, proportion, real_cas, substance_key
from camds_imds_importer.camds.api import CamdsApiError
from camds_imds_importer.camds.import_control import ImportControl
from camds_imds_importer.camds.tree_import import TreeImporter


def fixture_tree():
    substance = {"uid": "s", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6", "percentage": 100, "children": []}
    material = {"uid": "m", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1", "weight_g": 5, "material_number": "M1", "children": [substance]}
    child = {"uid": "c", "node_type": "COMPONENT", "name": "Child", "weight_g": 5, "quantity": 2, "children": [material]}
    return {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 10, "children": [child]}


def test_mass_quantity_and_composition_preflight():
    root = fixture_tree()
    assert ImportRequest(root).validate() == []
    # A source whose masses do not add up is reported, not refused: the values
    # are written as declared and CAMDS computes its own deviation.
    root["children"][0]["quantity"] = 1
    warnings = ImportRequest(root).validate()
    assert any("children total" in w and "imported as declared" in w for w in warnings)
    # A missing quantity is different: there is nothing to write.
    root["children"][0]["quantity"] = None
    with pytest.raises(ValueError, match="quantity"):
        ImportRequest(root).validate()


def test_composition_that_does_not_close_is_reported_and_still_imported():
    root = fixture_tree()
    root["children"][0]["children"][0]["children"][0]["percentage"] = 90
    request = ImportRequest(root)
    warnings = request.validate()
    assert any("composition totals 90-90%" in w for w in warnings)
    assert request.warnings == warnings


def test_a_second_rest_is_still_refused_because_it_cannot_be_entered():
    root = fixture_tree()
    material = root["children"][0]["children"][0]
    material["children"] = [
        {"uid": "s1", "node_type": "SUBSTANCE", "name": "A", "cas_number": "7439-89-6", "is_rest": True, "children": []},
        {"uid": "s2", "node_type": "SUBSTANCE", "name": "B", "cas_number": "7440-50-8", "is_rest": True, "children": []}]
    with pytest.raises(ValueError, match="Rest only once"):
        ImportRequest(root).validate()


def test_mapping_existing_material_does_not_confuse_imds_id_with_camds_id():
    root = fixture_tree()
    mat = root["children"][0]["children"][0]
    mat["classification"] = "8.4"
    mat["imds_id"] = "123456"
    with pytest.raises(ValueError, match="map an existing"):
        ImportRequest(root).validate()
    with pytest.raises(ValueError, match="exact CAMDS"):
        ImportRequest(root, {"m": ("123456", "1")}).validate()
    ImportRequest(root, {"m": ("CA_8_123456", "1")}).validate()


@pytest.mark.parametrize("changes", [
    {"percentage": float('nan')},
    {"percentage": None, "percentage_min": 20, "percentage_max": 10},
    {"cas_number": "system", "name": "  "},
])
def test_invalid_substance_blocks_full_tree_before_creation(changes):
    root = fixture_tree()
    root["children"][0]["children"][0]["children"][0].update(changes)
    with pytest.raises(ValueError):
        ImportRequest(root).validate()


def test_rest_with_its_resolved_value_is_a_rest_portion():
    # IMDS prints "Rest 7.98": Rest is the mode, 7.98 the value it resolves to.
    node = {"name": "Misc., not to declare", "is_rest": True, "percentage": 7.98}
    assert proportion(node) == ("rest",)
    root = fixture_tree()
    root["children"][0]["children"][0]["children"][0].update({"is_rest": True, "percentage": 100})
    ImportRequest(root).validate()


def test_system_group_without_a_cas_is_looked_up_by_name():
    root = fixture_tree()
    substance = root["children"][0]["children"][0]["children"][0]
    substance.update({"cas_number": "system", "name": "Misc., not to declare"})
    ImportRequest(root).validate()
    assert real_cas(substance) is None
    assert substance_key(substance) == "name:misc., not to declare"


def test_system_groups_with_different_names_are_not_treated_as_one_substance():
    # They share the "system" placeholder but are different declarations.
    pigment = {"cas_number": "system", "name": "Pigment portion, not to declare"}
    additives = {"cas_number": "system", "name": "Further Additives, not to declare"}
    assert substance_key(pigment) != substance_key(additives)


@pytest.mark.parametrize("portions, expected", [
    # Fixed portions of the same substance add up.
    ([{"percentage": 30.0}, {"percentage": 20.0}], {"percentage": 50.0}),
    # Ranges add bound by bound.
    ([{"percentage_min": 1.0, "percentage_max": 3.0}, {"percentage_min": 2.0, "percentage_max": 4.0}],
     {"percentage_min": 3.0, "percentage_max": 7.0}),
    # Rest absorbs: the remainder is still the remainder.
    ([{"percentage": 10.0}, {"is_rest": True}], {"is_rest": True, "percentage": None}),
])
def test_repeated_substances_are_combined_by_adding_their_portions(portions, expected):
    children = [dict({"uid": f"s{i}", "node_type": "SUBSTANCE", "name": "Iron",
                      "cas_number": "7439-89-6", "children": []}, **p) for i, p in enumerate(portions)]
    material = {"uid": "m", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1",
                "weight_g": 5, "children": children}
    request = ImportRequest(material).snapshot()
    merged = request.root["children"]
    assert len(merged) == 1
    for key, value in expected.items():
        assert merged[0][key] == value
    assert request.merges and "merged 2 entries of Iron" in request.merges[0]


def test_combining_leaves_a_composition_that_validates_and_is_planned_once():
    children = [{"uid": "a", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6",
                 "percentage": 40.0, "children": []},
                {"uid": "b", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6",
                 "percentage": 60.0, "children": []}]
    material = {"uid": "m", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1",
                "weight_g": 5, "children": children}
    request = ImportRequest(material).snapshot()
    request.validate()
    assert [step.action for step in request.plan()] == ["create_material", "add_substance"]


def test_existing_material_composition_is_never_rewritten_by_merging():
    children = [{"uid": "a", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6",
                 "percentage": 40.0, "children": []},
                {"uid": "b", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6",
                 "percentage": 60.0, "children": []}]
    material = {"uid": "m", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1",
                "weight_g": 5, "children": children}
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 5, "children": [material]}
    request = ImportRequest(root, {"m": ("CA_8_1", "0.01")}).snapshot()
    assert len(request.root["children"][0]["children"]) == 2
    assert not request.merges


def test_snapshot_is_detached_from_parser_edits():
    root = fixture_tree()
    request = ImportRequest(root).snapshot()
    key = request.fingerprint
    root["name"] = "Changed in parser"
    assert request.root["name"] == "Parent"
    assert request.fingerprint == key


class FakeDraftBrowser:

    async def can_reenter_saved(self):
        return False

    async def saved_children(self, path, at=(0, 1)):
        return []

    async def find_existing_component(self, node, resolved):
        return None

    async def find_existing_material(self, node):
        return None

    async def read_back_findings(self):
        return []
    def __init__(self, fail_save=None, fail_readback=False):
        self.calls = []
        self.saves = 0
        self.fail_save = fail_save
        self.fail_readback = fail_readback
        self.current_ref = None

    async def prepare(self):
        pass

    async def create_root(self, node, on_allocated=None):
        self.calls.append(("create", node["uid"]))
        self.current_ref = ("CA_8_100" if node["node_type"] == "MATERIAL" else "CA_5_200", "0.01")
        if on_allocated:
            on_allocated(self.current_ref)
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

    async def verify_child_count(self, path, count, at=(0, 1)):
        self.calls.append(("verify_count", count))

    async def verify_substance(self, path, node, at=(0, 1)):
        self.calls.append(("verify_cas", node["cas_number"]))

    async def select(self, path, at=(0, 1)):
        self.calls.append(("select", path[-1]))
        if path[-1] == "Steel":
            self.current_ref = ("CA_8_100", "0.01")

    async def add_semicomponent(self, path, node, at=(0, 1)):
        self.calls.append(("semi", node["uid"])) if hasattr(self, "calls") else None
        if hasattr(self, "addressed"):
            self.addressed.append(("semi", tuple(path), at))

    async def add_component(self, path, node, at=(0, 1), reuse_index=None):
        self.calls.append(("child", node["uid"]))

    async def add_material(self, path, node, ref, at=(0, 1), by_portion=False, reuse_index=None):
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
    root["children"][0]["node_type"] = "MODULE"
    backend = FakeDraftBrowser()
    with pytest.raises(ValueError):
        await TreeImporter(backend, tmp_path).run(ImportRequest(root))
    assert not backend.calls
    assert not list(tmp_path.iterdir())


# One substance of the report, named by 132 characters of a longer name that
# IMDS did not print. It is what stopped a live run 24 minutes in.
CUT_NAME = ('ISO 1043-4 FR(17) aromatic brominated compounds (excluding brominated diphenyl ether and biphenyls) in combination with antimony com')


def cut_name_tree():
    """Two Materials under one Component; only one of them cannot be composed."""
    unbuildable = {"uid": "m2", "node_type": "MATERIAL", "name": "PBT", "classification": "5.1.b",
                   "weight_g": 5, "children": [
                       {"uid": "s2", "node_type": "SUBSTANCE", "name": CUT_NAME,
                        "cas_number": None, "percentage": 100, "children": []}]}
    root = fixture_tree()
    root["children"][0]["children"].append(unbuildable)
    root["children"][0]["weight_g"] = 10
    root["weight_g"] = 20
    return root


class Catalogue(FakeDraftBrowser):
    """A backend that answers the substance lookup, as the API one does."""

    def __init__(self, unknown=(), **kw):
        super().__init__(**kw)
        self.unknown = set(unknown)
        self.asked = []

    async def add_substance(self, name, node):
        # As the real backend does: a substance is resolved when it is added.
        await self.resolve_substance(node)
        return await super().add_substance(name, node)

    async def resolve_substance(self, node):
        self.asked.append(node["name"])
        if node["name"] in self.unknown:
            raise CamdsApiError(f"{node['name']}: matched 0 entries exactly, not one.")
        return {"csid": "1", "enName": node["name"]}


def answering(reply):
    """An operator at the other end of the question, answering straight away."""
    seen = []

    def ask(text):
        seen.append(text)
        control.answer(reply)

    control = ImportControl()
    return control, ask, seen


async def test_only_a_name_imds_cut_short_is_looked_up_before_the_run(tmp_path):
    """Every other substance is looked up where it always was, as it is added:
    checking all of them again here would double a run of hours."""
    backend = Catalogue()
    await TreeImporter(backend, tmp_path).run(ImportRequest(cut_name_tree()))
    assert backend.asked[0] == CUT_NAME, "the cut name is settled before anything is created"
    assert backend.asked.count(CUT_NAME) == 2, "then resolved again where it is added"
    assert "Iron" not in backend.asked[:1], "a whole name is not looked up ahead of time"


async def test_a_material_that_cannot_be_composed_is_put_to_the_operator(tmp_path):
    """Skipping is their decision: what CAMDS holds is not what the report
    describes, and that is a judgement about the data."""
    backend = Catalogue(unknown={CUT_NAME})
    control, ask, asked = answering(True)
    importer = TreeImporter(backend, tmp_path, control=control, ask=ask)
    result = await importer.run(ImportRequest(cut_name_tree()))

    assert len(asked) == 1 and "cut their names short" in asked[0]
    assert CUT_NAME in asked[0] and "PBT" in asked[0]
    assert ("create", "m2") not in backend.calls, "nothing of it reached CAMDS"
    assert ("create", "m") in backend.calls, "the rest of the tree is imported"
    assert any("PBT: not imported, at Parent / Child / PBT" in note
               for note in result["skipped"]), result["skipped"]
    assert any("could not be identified" in note and "PBT" in note
               for note in result["skipped"]), "where it was, and why it went"


async def test_stopping_leaves_the_run_exactly_where_it_was(tmp_path):
    """The other answer. Nothing was created before the question, so nothing
    has to be undone after it."""
    backend = Catalogue(unknown={CUT_NAME})
    control, ask, _ = answering(False)
    with pytest.raises(CamdsApiError, match="matched 0 entries"):
        await TreeImporter(backend, tmp_path, control=control, ask=ask).run(
            ImportRequest(cut_name_tree()))
    assert not [call for call in backend.calls if call[0] == "create"]
    assert not list(tmp_path.glob("*.jsonl")), "no journal for a run that never started"


async def test_with_nobody_to_ask_the_run_stops_as_it_always_did(tmp_path):
    """A question nobody can answer is not a reason to import wrong data."""
    backend = Catalogue(unknown={CUT_NAME})
    with pytest.raises(CamdsApiError):
        await TreeImporter(backend, tmp_path).run(ImportRequest(cut_name_tree()))


async def test_a_substance_whose_whole_name_does_not_match_still_stops_the_run(tmp_path):
    """Nothing about it is unclear: the name is complete and CAMDS has no such
    entry. There is no judgement for a person to make."""
    root = fixture_tree()
    root["children"][0]["children"][0]["children"][0]["cas_number"] = None
    backend = Catalogue(unknown={"Iron"})
    control, ask, asked = answering(True)
    with pytest.raises(CamdsApiError):
        await TreeImporter(backend, tmp_path, control=control, ask=ask).run(ImportRequest(root))
    assert asked == [], "not a question anyone was asked"


def test_where_a_skipped_material_was_is_reported_by_its_path():
    """A tree with something missing from it and no word about what: the one
    thing nobody can see by looking at what was imported."""
    request = ImportRequest(cut_name_tree()).snapshot()
    removed = request.without({"m2"})
    assert removed == ["PBT: not imported, at Parent / Child / PBT"]
    assert [m["uid"] for m in request.materials()] == ["m"]


def test_a_parent_left_holding_nothing_goes_with_it():
    """An empty Component describes nothing, and CAMDS refuses an empty
    Semicomponent outright."""
    root = cut_name_tree()
    # A second branch, so emptying the first does not empty the whole import.
    other = copy.deepcopy(root["children"][0])
    for node, uid in ((other, "c3"), (other["children"][0], "m3"),
                      (other["children"][0]["children"][0], "s3")):
        node["uid"] = uid
    other["name"] = "Other"
    root["children"].append(other)
    del other["children"][1]

    request = ImportRequest(root).snapshot()
    removed = request.without({"m", "m2"})
    assert removed == ["Steel: not imported, at Parent / Child / Steel",
                       "PBT: not imported, at Parent / Child / PBT",
                       "Child: Component left with nothing in it, so it was not imported "
                       "either, at Parent / Child"]
    assert [child["name"] for child in request.root["children"]] == ["Other"]


def test_skipping_everything_is_refused_rather_than_importing_an_empty_tree():
    request = ImportRequest(fixture_tree()).snapshot()
    with pytest.raises(ValueError, match="nothing to import"):
        request.without({"m"})


async def test_stop_is_answered_while_a_question_is_on_screen(tmp_path):
    """The dialog is modal, so Stop cannot be reached through the buttons; the
    run watches for it as it waits."""
    import asyncio

    from camds_imds_importer.camds.import_control import ImportStopped

    control = ImportControl()
    waiting = asyncio.ensure_future(control.ask("Skip them?", lambda text: None))
    await asyncio.sleep(0.05)
    assert control.question == "Skip them?", "the UI can see what is being asked"
    control.stop()
    with pytest.raises(ImportStopped):
        await asyncio.wait_for(waiting, timeout=2)


class Numbered(FakeDraftBrowser):
    """A CAMDS that answers a Component search by number."""

    def __init__(self, held=None, **kw):
        super().__init__(**kw)
        self.held = held or {}          # part number -> (mds id, version)
        self.attached = {}              # name in the tree -> what is attached there
        self.asked = []
        # What the saved tree holds under each path, in the order it was added.
        # The read-back asks CAMDS this, and a reference is recognised in it by
        # position and by the MDS it points at, never by its name.
        self.saved = {}

    def _record(self, parent_path, name, mds=None):
        self.saved.setdefault(tuple(parent_path), []).append({"name": name, "mds": mds})

    async def saved_children(self, path, at=(0, 1)):
        return list(self.saved.get(tuple(path), []))

    async def add_component(self, path, node, at=(0, 1), reuse_index=None):
        self._record(path, node["name"])
        return await super().add_component(path, node, at, reuse_index)

    async def add_material(self, path, node, ref, at=(0, 1), by_portion=False, reuse_index=None):
        self._record(path, node["name"], ref[0])
        return await super().add_material(path, node, ref, at, by_portion, reuse_index)

    async def find_component_by_number(self, node):
        self.asked.append(node.get("part_number"))
        return self.held.get(str(node.get("part_number") or ""))

    async def add_component_reference(self, parent_path, child, ref, at=(0, 1)):
        self.calls.append(("attach", child["uid"], tuple(ref)))
        # Siblings can share a name, so what is attached is kept in the order it
        # was attached and addressed by position, as CAMDS's own tree is.
        self.attached.setdefault(child["name"], []).append(tuple(ref))
        # CAMDS labels a reference after the MDS it points at, not after the
        # name the report gives the position.
        self._record(parent_path, "whatever CAMDS calls " + ref[0], ref[0])
        return child["name"]

    async def select(self, path, at=(0, 1)):
        # As CAMDS does: selecting an attached node shows that MDS's identity.
        await super().select(path, at)
        here = self.attached.get(path[-1]) or []
        if at[0] < len(here):
            self.current_ref = here[at[0]]


def two_level_tree():
    """A sub-assembly with a Material inside it, under a parent Component."""
    substance = {"uid": "s", "node_type": "SUBSTANCE", "name": "Iron",
                 "cas_number": "7439-89-6", "percentage": 100, "children": []}
    material = {"uid": "m", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1",
                "weight_g": 5, "children": [substance]}
    inner = {"uid": "c", "node_type": "COMPONENT", "name": "Bracket", "part_number": "1234567890",
             "weight_g": 5, "quantity": 1, "children": [material]}
    return {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "part_number": "9999999999",
            "weight_g": 5, "children": [inner]}


async def test_a_component_already_in_camds_is_attached_and_not_built(tmp_path):
    """The point of the mode: a sub-assembly CAMDS already holds costs one
    reference, not the Materials inside it."""
    backend = Numbered({"1234567890": ("CA_5_777", "3")})
    result = await TreeImporter(backend, tmp_path).run(
        ImportRequest(two_level_tree()), components="number")

    assert ("attach", "c", ("CA_5_777", "3")) in backend.calls
    assert not [call for call in backend.calls if call[0] == "create" and call[1] == "m"], \
        "nothing inside it was created"
    assert not [call for call in backend.calls if call[0] == "child"], "nor the Component itself"
    assert any("attached CA_5_777/3" in note and "1234567890" in note
               for note in result["skipped"]), result["skipped"]


async def test_the_root_is_built_even_when_its_number_is_in_camds(tmp_path):
    """Attaching the root to itself would import nothing at all."""
    backend = Numbered({"9999999999": ("CA_5_1", "1"), "1234567890": ("CA_5_777", "3")})
    await TreeImporter(backend, tmp_path).run(ImportRequest(two_level_tree()),
                                              components="number")
    assert ("create", "r") in backend.calls
    assert "9999999999" not in backend.asked


async def test_a_number_camds_does_not_hold_is_built_as_usual(tmp_path):
    backend = Numbered({})
    await TreeImporter(backend, tmp_path).run(ImportRequest(two_level_tree()),
                                              components="number")
    assert ("create", "m") in backend.calls
    assert ("child", "c") in backend.calls


async def test_the_mode_is_off_unless_it_is_asked_for(tmp_path):
    """The strict rule stays the default: the number alone is a looser identity
    than the contents, and choosing it is the operator's call."""
    backend = Numbered({"1234567890": ("CA_5_777", "3")})
    await TreeImporter(backend, tmp_path).run(ImportRequest(two_level_tree()))
    assert backend.asked == [], "not asked at all"
    assert ("create", "m") in backend.calls


def test_resume_carries_the_version_a_release_gave_a_material(tmp_path):
    """Releasing bumps 0.01 to 1. A resumed run that restored the draft version
    would attach a version CAMDS has superseded - all 70 released Materials of
    the run of 2026-09-09 came back as 0.01."""
    from camds_imds_importer.camds.tree_import import read_journal

    path = tmp_path / "run.jsonl"
    path.write_text("\n".join(json.dumps(entry) for entry in [
        {"event": "started", "fingerprint": "f"},
        {"event": "material_id_allocated", "uid": "m", "ref": ["CA_8_1", "0.01"]},
        {"event": "material_readback_verified", "uid": "m", "ref": ["CA_8_1", "0.01"],
         "display_name": "Steel"},
        {"event": "release_requested", "uid": "m", "ref": ["CA_8_1", "0.01"]},
        {"event": "released", "uid": "m", "ref": ["CA_8_1", "1"]},
    ]), encoding="utf-8")

    state = read_journal(path)
    assert state.material_refs["m"] == ("CA_8_1", "1")
    assert state.completed == {"m"}
    assert state.incomplete_materials == {}
    assert state.names["m"] == "Steel"


def test_a_material_that_was_never_released_keeps_the_version_it_has(tmp_path):
    from camds_imds_importer.camds.tree_import import read_journal

    path = tmp_path / "run.jsonl"
    path.write_text("\n".join(json.dumps(entry) for entry in [
        {"event": "started", "fingerprint": "f"},
        {"event": "material_readback_verified", "uid": "m", "ref": ["CA_8_1", "0.01"]},
    ]), encoding="utf-8")
    assert read_journal(path).material_refs["m"] == ("CA_8_1", "0.01")


def repeated_siblings_tree(count=3, attach_index=1):
    """A board carrying several Components of the same name, one of them
    already in CAMDS under its own number."""
    def resistor(i):
        return {"uid": f"c{i}", "node_type": "COMPONENT", "name": "RESISTOR",
                "part_number": f"890900{i:04d}", "weight_g": 1.0, "quantity": 1,
                "children": [{"uid": f"m{i}", "node_type": "MATERIAL", "name": "Steel",
                              "classification": "1.1.1", "weight_g": 1.0, "children": [
                                  {"uid": f"s{i}", "node_type": "SUBSTANCE", "name": "Iron",
                                   "cas_number": "7439-89-6", "percentage": 100,
                                   "children": []}]}]}
    board = {"uid": "b", "node_type": "COMPONENT", "name": "PCBA", "part_number": "1111111111",
             "weight_g": float(count), "quantity": 1,
             "children": [resistor(i) for i in range(count)]}
    return {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "part_number": "9999999999",
            "weight_g": float(count), "children": [board]}


class Renaming(Numbered):
    """A CAMDS whose tree holds the attachment at one position among siblings
    that share a name, so selecting the others must not find it."""

    def __init__(self, held=None, attached_at=None, **kw):
        super().__init__(held, **kw)
        self.attached_at = attached_at      # the occurrence that was attached
        self.reference = None
        self.under = []                     # where each Material was attached

    async def add_component_reference(self, parent_path, child, ref, at=(0, 1)):
        self.calls.append(("attach", child["uid"], tuple(ref), tuple(parent_path), at))
        self.reference = tuple(ref)
        self._record(parent_path, "whatever CAMDS calls " + ref[0], ref[0])
        return child["name"]

    async def select(self, path, at=(0, 1)):
        await super().select(path, at)
        if self.reference and at[0] == self.attached_at and path[-1] == "RESISTOR":
            self.current_ref = self.reference

    async def add_material(self, parent_path, node, ref, at=(0, 1), by_portion=False,
                           reuse_index=None):
        self.under.append((tuple(parent_path), at))
        return await super().add_material(parent_path, node, ref, at, by_portion, reuse_index)


async def test_an_attached_component_still_counts_among_its_own_siblings(tmp_path):
    """Thirteen Components named RESISTOR, one of them attached: addressing the
    tenth found nine, and 1h38m of a live run ended there."""
    backend = Renaming({"8909000001": ("CA_5_777", "2")}, attached_at=1)
    result = await TreeImporter(backend, tmp_path).run(
        ImportRequest(repeated_siblings_tree(count=3, attach_index=1)), components="number")

    attached = [call for call in backend.calls if call[0] == "attach"]
    assert [call[1] for call in attached] == ["c1"], "the middle sibling was attached"
    # Every sibling after the attached one must still be addressed by its own
    # position among all three, which is what stopped working.
    assert backend.under == [(("Parent", "PCBA", "RESISTOR"), (0, 3)),
                             (("Parent", "PCBA", "RESISTOR"), (2, 3))], backend.under
    assert result["nodes"] == result["total"]


def test_an_attached_component_is_recognised_by_what_it_points_at():
    """On a resume, comparing its name would compare the report's name against
    the name of the MDS it references, and attach a second copy."""
    from camds_imds_importer.camds.tree_import import _child_is

    child = {"uid": "c1", "node_type": "COMPONENT", "name": "RESISTOR"}
    saved = {"name": "Chip resistor 10k 0402", "mds": "CA_5_777"}
    assert not _child_is(child, saved, {}), "without knowing it is a reference"
    assert _child_is(child, saved, {}, {"c1": ("CA_5_777", "2")})
    assert not _child_is(child, {"name": "x", "mds": "CA_5_999"}, {},
                         {"c1": ("CA_5_777", "2")}), "a different MDS is a different part"


async def test_the_same_component_twice_under_one_parent_becomes_one_with_both_quantities(tmp_path):
    """In CAMDS the MDS id is the unit. The report lists "Fixed thick film chip
    resistor", part 8905501369, twice under one board - once and 22 times."""
    def resistor(uid, quantity):
        return {"uid": uid, "node_type": "COMPONENT", "name": "Fixed thick film chip resistor",
                "part_number": "8905501369", "weight_g": 2.0, "quantity": quantity,
                "children": [{"uid": "m" + uid, "node_type": "MATERIAL", "name": "Steel",
                              "classification": "1.1.1", "weight_g": 2.0, "children": [
                                  {"uid": "s" + uid, "node_type": "SUBSTANCE", "name": "Iron",
                                   "cas_number": "7439-89-6", "percentage": 100,
                                   "children": []}]}]}
    board = {"uid": "b", "node_type": "COMPONENT", "name": "PCBA", "part_number": "1111111111",
             "weight_g": 46.0, "quantity": 1,
             "children": [resistor("c1", 1.0), resistor("c2", 22.0)]}
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "part_number": "9999999999",
            "weight_g": 46.0, "children": [board]}

    backend = Numbered({"8905501369": ("CA_5_777", "2")})
    result = await TreeImporter(backend, tmp_path).run(ImportRequest(root), components="number")

    attached = [call for call in backend.calls if call[0] == "attach"]
    assert [call[1] for call in attached] == ["c1"], "one node, not two"
    assert result["nodes"] == result["total"]
    assert any("quantity 23" in note for note in result["skipped"]), result["skipped"]


async def test_two_different_components_are_never_merged(tmp_path):
    """Same name, different part numbers: two parts, two nodes."""
    def resistor(uid, number):
        return {"uid": uid, "node_type": "COMPONENT", "name": "RESISTOR",
                "part_number": number, "weight_g": 1.0, "quantity": 1,
                "children": [{"uid": "m" + uid, "node_type": "MATERIAL", "name": "Steel",
                              "classification": "1.1.1", "weight_g": 1.0, "children": [
                                  {"uid": "s" + uid, "node_type": "SUBSTANCE", "name": "Iron",
                                   "cas_number": "7439-89-6", "percentage": 100,
                                   "children": []}]}]}

    board = {"uid": "b", "node_type": "COMPONENT", "name": "PCBA", "part_number": "1111111111",
             "weight_g": 2.0, "quantity": 1,
             "children": [resistor("c1", "8905501369"), resistor("c2", "8905501370")]}
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "part_number": "9999999999",
            "weight_g": 2.0, "children": [board]}

    backend = Numbered({"8905501369": ("CA_5_777", "2"), "8905501370": ("CA_5_778", "1")})
    await TreeImporter(backend, tmp_path).run(ImportRequest(root), components="number")
    assert [call[1] for call in backend.calls if call[0] == "attach"] == ["c1", "c2"]


class Reusing(FakeDraftBrowser):
    """A CAMDS that already holds the Material, whatever it is called here."""

    REF = ("CA_8_500", "2")

    async def find_existing_material(self, node):
        return self.REF

    async def value(self, label):
        return "Steel"

    async def select(self, path, at=(0, 1)):
        await super().select(path, at)
        if path[-1] == "Steel":
            self.current_ref = self.REF


async def test_the_same_material_twice_under_one_parent_becomes_one_carrying_both(tmp_path):
    """A Material has an MDS id too, and CAMDS holds one node per id at a
    level. Two siblings that resolve to the same one are written once, with the
    mass of both: "Die Attach Adhesive (Ag)" appears twice under one IC."""
    def adhesive(uid, mass):
        return {"uid": uid, "node_type": "MATERIAL", "name": "Die Attach Adhesive (Ag)",
                "classification": "1.1.1", "weight_g": mass, "children": [
                    {"uid": "s" + uid, "node_type": "SUBSTANCE", "name": "Iron",
                     "cas_number": "7439-89-6", "percentage": 100, "children": []}]}

    ic = {"uid": "ic", "node_type": "COMPONENT", "name": "IC", "part_number": "IC1",
          "weight_g": 0.000597, "quantity": 1,
          "children": [adhesive("m1", 0.0002985), adhesive("m2", 0.0002985)]}
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "part_number": "P1",
            "weight_g": 0.000597, "children": [ic]}

    backend = Reusing()
    result = await TreeImporter(backend, tmp_path).run(ImportRequest(root))

    attached = [call for call in backend.calls if call[0] == "reference"]
    assert [call[1] for call in attached] == ["m1"], "one node, not two"
    assert any("mass 0.000597 g" in note for note in result["skipped"]), result["skipped"]
    # The mass of both, so the IC still weighs what the report says it weighs.
    assert any("Die Attach Adhesive (Ag)" in note for note in result["skipped"])


async def test_the_same_material_at_two_levels_is_left_alone(tmp_path):
    """CAMDS refuses two of one id at a level, not in a tree. The same Material
    under two different Components is two ordinary references."""
    def steel(uid):
        return {"uid": uid, "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1",
                "weight_g": 1.0, "children": [
                    {"uid": "s" + uid, "node_type": "SUBSTANCE", "name": "Iron",
                     "cas_number": "7439-89-6", "percentage": 100, "children": []}]}

    inner = {"uid": "c", "node_type": "COMPONENT", "name": "Child", "part_number": "C1",
             "weight_g": 1.0, "quantity": 1, "children": [steel("m2")]}
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "part_number": "P1",
            "weight_g": 2.0, "children": [steel("m1"), inner]}

    backend = Reusing()
    result = await TreeImporter(backend, tmp_path).run(ImportRequest(root))
    assert [call[1] for call in backend.calls if call[0] == "reference"] == ["m1", "m2"]
    assert not [note for note in result["skipped"] if "listed again" in note]


async def test_a_run_that_reused_everything_still_finishes_at_the_full_count(tmp_path):
    """A finished import said "28 / 42" and left the bar at two thirds.

    The plan is made before CAMDS is asked anything, so it budgets a step for
    every substance of every Material. A Material found already in CAMDS costs
    one step instead, and those substance steps are then not work left undone.
    """
    class Reused(FakeDraftBrowser):
        async def find_existing_material(self, node):
            return ("CA_8_500", "2")

        async def value(self, label):
            return "Steel"

        async def select(self, path, at=(0, 1)):
            await super().select(path, at)
            if path[-1] == "Steel":
                self.current_ref = ("CA_8_500", "2")

    root = fixture_tree()
    material = root["children"][0]["children"][0]
    material["children"] = [
        {"uid": f"s{i}", "node_type": "SUBSTANCE", "name": f"Sub {i}", "cas_number": None,
         "percentage": 25.0, "children": []} for i in range(4)]

    planned = len(ImportRequest(root).snapshot().plan())
    result = await TreeImporter(Reused(), tmp_path).run(ImportRequest(root))

    assert result["nodes"] == result["total"], f"{result['nodes']} of {result['total']}"
    assert result["total"] == planned - 4, "the four substances were never going to be added"


async def test_the_total_never_drops_below_what_was_already_done(tmp_path):
    """A count that walks backwards past the work already done would be worse
    than one that overshoots."""
    from camds_imds_importer.camds.import_control import Reporter

    reporter = Reporter(10, lambda event: None)
    for _ in range(6):
        reporter.done()
    reporter.drop(9)
    assert (reporter.completed, reporter.total) == (6, 6)
    reporter.drop(-3)
    assert reporter.total == 6, "a negative drop is not an increase"


async def test_the_read_back_asks_what_the_attached_position_points_at(tmp_path):
    """The saved tree calls a reference after the MDS it points at, so there is
    no name that addresses it both while building and when reading back. The
    position is the same in both trees, and the MDS is the whole question - a
    live run stopped at "expected at least 1 node(s), CAMDS has 0" for want of
    that."""
    class Moved(Numbered):
        """CAMDS holds something else where the attachment should be."""

        def _record(self, parent_path, name, mds=None):
            super()._record(parent_path, name, "CA_5_999" if mds == "CA_5_777" else mds)

    root = two_level_tree()
    with pytest.raises(RuntimeError, match="CAMDS has CA_5_999 at position 1"):
        await TreeImporter(Moved({"1234567890": ("CA_5_777", "3")}), tmp_path).run(
            ImportRequest(root), components="number")


class Existing(FakeDraftBrowser):
    """A CAMDS holding one Component the operator wants the tree written into."""

    def __init__(self, held=(), **kw):
        super().__init__(**kw)
        self.held = list(held)
        self.opened = []
        self.created_roots = 0

    async def open_saved(self, kind, ref):
        self.opened.append((kind, tuple(ref)))
        await super().open_saved(kind, ref)

    async def saved_children(self, path, at=(0, 1)):
        return list(self.held) if not path else []

    async def create_root(self, node, on_allocated=None, existing=None):
        if existing is None:
            # Materials still get their own MDS; what must not be allocated is
            # a second Component to hold the tree.
            self.created_roots += node["node_type"] == "COMPONENT"
            return await super().create_root(node, on_allocated)
        self.calls.append(("fill", node["uid"], tuple(existing)))
        self.current_ref = tuple(existing)
        return tuple(existing)


async def test_the_tree_is_written_into_the_component_that_was_given(tmp_path):
    """No new Component is created; the one named is filled and the tree built
    inside it."""
    backend = Existing()
    result = await TreeImporter(backend, tmp_path).run(
        ImportRequest(fixture_tree()), root_ref=("CA_5_4242", "0.01"))

    assert backend.created_roots == 0, "nothing new was allocated"
    assert ("fill", "r", ("CA_5_4242", "0.01")) in backend.calls
    assert result["identity"] == "CA_5_4242/0.01"
    assert any("written into CA_5_4242/0.01" in note for note in result["skipped"])
    events = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text().splitlines()]
    kinds = [e["event"] for e in events]
    assert "parent_id_allocated" in kinds, "a resume has to find the id this run wrote into"
    assert "create_parent_requested" not in kinds


async def test_a_component_that_already_holds_something_is_refused(tmp_path):
    """Filling it would put a second copy of what is in there beside the first,
    and from here one cannot be told from the other."""
    backend = Existing(held=[{"name": "Bracket", "mds": "CA_5_9"},
                             {"name": "Steel", "mds": "CA_8_9"}])
    with pytest.raises(RuntimeError, match="already holds 2 node"):
        await TreeImporter(backend, tmp_path).run(
            ImportRequest(fixture_tree()), root_ref=("CA_5_4242", "0.01"))

    assert backend.created_roots == 0
    assert not [call for call in backend.calls if call[0] == "fill"], "nothing was written"


async def test_a_material_root_cannot_be_written_into_a_component(tmp_path):
    root = fixture_tree()["children"][0]["children"][0]
    with pytest.raises(RuntimeError, match="names a Component"):
        await TreeImporter(Existing(), tmp_path).run(
            ImportRequest(root), root_ref=("CA_5_4242", "0.01"))


async def test_a_typed_id_that_contradicts_the_journal_is_refused(tmp_path):
    """Two answers to "where does this run write", and no way to choose."""
    from camds_imds_importer.camds.tree_import import ResumeState

    importer = TreeImporter(Existing(), tmp_path)
    state = ResumeState(root_ref=("CA_5_1", "0.01"))
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "children": []}

    with pytest.raises(RuntimeError, match="Clear one of the two"):
        await importer._empty_root(root, ("CA_5_2", "0.01"), state)

    # The same id is no contradiction; the resume path opens it.
    assert await importer._empty_root(root, ("CA_5_1", "0.01"), state) is None
