"""Finding a Material in CAMDS instead of making another one.

Creating one per run filled the account with duplicates. What makes a Material
the same one, by instruction, is what it is: the same name, the same
substances, and the same portion of each.

A name is not an identity - one report calls two different Materials "Ep-Ni" -
so the composition settles it. A Material No. is not required, because the
reports that prompted this carry none at all; where there is one it narrows the
search rather than deciding it.
"""
import pytest

from camds_imds_importer.camds.api import CamdsApi
from camds_imds_importer.camds.api_backend import ApiBackend


class Camds:
    """Answers the three calls a lookup makes, the way CAMDS does."""

    def __init__(self, rows=(), trees=None, portions=None):
        self.rows, self.trees = list(rows), trees or {}
        self.portions = portions or {}
        self.asked = []

    async def post(self, url, params=None, data=None, headers=None, timeout=None):
        name = url.rsplit("/", 1)[-1]
        self.asked.append((name, params or {}, data or {}))
        if name == "findMaterialByCondition":
            payload = {"records": self.rows}
        elif name == "loadMdsTree":
            payload = self.trees.get(params["mdsId"])
        elif name == "loadNodeDate":
            payload = {"data": {}, "structureVO": self.portions.get(params["strutsId"], {}),
                       "treeDataNode": {}}
        else:
            payload = None

        class Response:
            status = 200

            @staticmethod
            async def json():
                return {"respCode": "0", "ok": True, "data": payload}
        return Response


def backend(camds):
    return ApiBackend(CamdsApi(camds, base_url="https://camds.test"))


def substance(uid="s", cas="7440-50-8", name="Copper", **portion):
    return dict({"uid": uid, "node_type": "SUBSTANCE", "name": name, "cas_number": cas,
                 "percentage": 100, "children": []}, **portion)


def material(name="Cu99", number=None, children=None):
    return {"uid": "m", "node_type": "MATERIAL", "name": name, "material_number": number,
            "classification": "1.1.1", "weight_g": 1.0,
            "children": children if children is not None else [substance()]}


def saved(name="Cu99", subs=(("CA_21_9", "7440-50-8", "Copper"),)):
    return {"id": "CA_21_1", "mdsId": "CA_8_9", "mdsCver": 1.0, "text": name,
            "children": [{"id": i, "cascode": cas, "cenName": label, "nodeType": 4}
                         for i, cas, label in subs]}


FIXED_100 = {"crateType": 2, "crate": 100, "cminRate": 0, "cmaxRate": 0}


async def test_a_material_with_the_same_name_and_composition_is_reused():
    camds = Camds(rows=[{"mdsId": "CA_8_9", "version": "1"}],
                  trees={"CA_8_9": saved()}, portions={"CA_21_9": FIXED_100})
    assert await backend(camds).find_existing_material(material()) == ("CA_8_9", "1")


async def test_a_material_with_no_number_is_still_looked_up():
    """The reports that prompted this carry no Material No. at all."""
    camds = Camds(rows=[{"mdsId": "CA_8_9", "version": "1"}],
                  trees={"CA_8_9": saved()}, portions={"CA_21_9": FIXED_100})
    assert await backend(camds).find_existing_material(material(number=None)) is not None
    assert camds.asked[0][2]["name"] == "Cu99"


async def test_a_material_code_fills_both_search_fields():
    camds = Camds()
    await backend(camds).find_existing_material(material(number="1274477721"))
    body = camds.asked[0][2]
    assert body["name"] == "Cu99" and body["symbol"] == "1274477721"


async def test_a_row_under_a_different_code_is_not_a_match():
    """CAMDS matches loosely, so the row is checked rather than trusted."""
    camds = Camds(rows=[{"mdsId": "CA_8_9", "version": "1", "symbol": "OTHER"}],
                  trees={"CA_8_9": saved()}, portions={"CA_21_9": FIXED_100})
    assert await backend(camds).find_existing_material(material(number="1274477721")) is None


async def test_a_different_name_is_not_a_match():
    camds = Camds(rows=[{"mdsId": "CA_8_9", "version": "1"}],
                  trees={"CA_8_9": saved(name="Something else")},
                  portions={"CA_21_9": FIXED_100})
    assert await backend(camds).find_existing_material(material()) is None


async def test_a_different_substance_list_is_not_a_match():
    """Two Materials in one report are both called "Ep-Ni". The name is not an
    identity; what they are made of is."""
    camds = Camds(rows=[{"mdsId": "CA_8_9", "version": "1"}],
                  trees={"CA_8_9": saved(subs=(("CA_21_9", "7440-02-0", "Nickel"),))},
                  portions={"CA_21_9": FIXED_100})
    assert await backend(camds).find_existing_material(material()) is None


async def test_one_substance_too_many_is_not_a_match():
    camds = Camds(rows=[{"mdsId": "CA_8_9", "version": "1"}],
                  trees={"CA_8_9": saved(subs=(("CA_21_9", "7440-50-8", "Copper"),
                                               ("CA_21_8", "7439-89-6", "Iron")))},
                  portions={"CA_21_9": FIXED_100, "CA_21_8": FIXED_100})
    assert await backend(camds).find_existing_material(material()) is None


async def test_the_same_substances_in_a_different_portion_are_not_a_match():
    camds = Camds(rows=[{"mdsId": "CA_8_9", "version": "1"}],
                  trees={"CA_8_9": saved()},
                  portions={"CA_21_9": {"crateType": 2, "crate": 40, "cminRate": 0, "cmaxRate": 0}})
    assert await backend(camds).find_existing_material(material()) is None


async def test_the_order_substances_were_added_in_does_not_matter():
    """A composition is a set, not a sequence."""
    report = material(children=[substance("a"), substance("b", cas="7439-89-6", name="Iron")])
    camds = Camds(rows=[{"mdsId": "CA_8_9", "version": "1"}],
                  trees={"CA_8_9": saved(subs=(("CA_21_8", "7439-89-6", "Iron"),
                                               ("CA_21_9", "7440-50-8", "Copper")))},
                  portions={"CA_21_9": FIXED_100, "CA_21_8": FIXED_100})
    assert await backend(camds).find_existing_material(report) == ("CA_8_9", "1")


async def test_a_substance_with_no_cas_is_matched_on_its_name():
    """A system group has no CAS, and CAMDS may relabel it, so the English name
    is what is compared - the same field read-back uses."""
    report = material(children=[substance(cas=None, name="Misc., not to declare")])
    camds = Camds(rows=[{"mdsId": "CA_8_9", "version": "1"}],
                  trees={"CA_8_9": saved(subs=(("CA_21_9", "system", "Misc., not to declare"),))},
                  portions={"CA_21_9": FIXED_100})
    assert await backend(camds).find_existing_material(report) == ("CA_8_9", "1")


async def test_a_draft_version_is_never_reused():
    """0.01 is a half-built draft, possibly one this tool left behind."""
    camds = Camds(rows=[{"mdsId": "CA_8_9", "version": "0.01"}],
                  trees={"CA_8_9": saved()}, portions={"CA_21_9": FIXED_100})
    assert await backend(camds).find_existing_material(material()) is None


async def test_the_newest_released_version_wins():
    camds = Camds(rows=[{"mdsId": "CA_8_old", "version": "1"},
                        {"mdsId": "CA_8_new", "version": "4"}],
                  trees={"CA_8_old": saved(), "CA_8_new": saved()},
                  portions={"CA_21_9": FIXED_100})
    assert await backend(camds).find_existing_material(material()) == ("CA_8_new", "4")


async def test_a_material_with_no_substances_is_not_looked_up():
    """There would be nothing to settle the match with."""
    camds = Camds()
    assert await backend(camds).find_existing_material(material(children=[])) is None
    assert camds.asked == []


async def test_rest_is_compared_as_a_mode_not_as_a_number():
    """CAMDS computes what Rest resolves to, so its value is its own."""
    report = material(children=[substance(percentage=7.98, is_rest=True)])
    camds = Camds(rows=[{"mdsId": "CA_8_9", "version": "1"}],
                  trees={"CA_8_9": saved()},
                  portions={"CA_21_9": {"crateType": 3, "crate": 9.5,
                                        "cminRate": 0, "cmaxRate": 0}})
    assert await backend(camds).find_existing_material(report) == ("CA_8_9", "1")


def test_one_portion_comparison_serves_the_search_and_the_read_back():
    """Two copies of a rule drift apart; this project has done it twice."""
    import inspect

    from camds_imds_importer.camds import api_backend

    assert "portion_disagreement" in inspect.getsource(api_backend.ApiBackend.verify_proportion)
    assert "portion_disagreement" in inspect.getsource(api_backend.ApiBackend._same_material)


async def test_reuse_is_reported_and_journalled(tmp_path):
    """Which Material a part points at is the operator's business."""
    import json

    from camds_imds_importer.camds.import_plan import ImportRequest
    from camds_imds_importer.camds.tree_import import TreeImporter

    class Backend:
        reporter = None

        async def prepare(self): pass
        async def read_back_findings(self): return []
        async def can_reenter_saved(self): return False
        async def saved_children(self, path, at=(0, 1)): return []
        async def find_existing_material(self, node): return ("CA_8_9", "6")
        async def find_existing_component(self, node, resolved): return None
        async def open_saved(self, kind, ref): self.ref = tuple(ref)
        async def value(self, label): return "Cu99"
        async def identity(self): return self.ref
        async def verify_value(self, label, expected): pass
        async def verify_child_count(self, path, count, at=(0, 1)): pass
        async def select(self, path, at=(0, 1)):
            # Read-back selects the attached Material and asks which MDS it is.
            if path[-1] == "Cu99":
                self.ref = ("CA_8_9", "6")
        async def create_root(self, node, on_allocated=None, existing=None):
            if node["node_type"] == "MATERIAL":
                raise AssertionError("a Material already in CAMDS must not be created again")
            self.ref = ("CA_5_1", "0.01")
            if on_allocated:
                on_allocated(self.ref)
            return self.ref
        async def save(self): pass
        async def add_material(self, path, node, ref, at=(0, 1), by_portion=False, reuse_index=None):
            self.ref = tuple(ref)
            return "Cu99"

    root = {"uid": "r", "node_type": "COMPONENT", "name": "Part", "weight_g": 1.0,
            "children": [material()]}
    result = await TreeImporter(Backend(), tmp_path).run(ImportRequest(root))

    assert any("reused CA_8_9/6" in note for note in result["skipped"]), result["skipped"]
    events = [json.loads(line) for line in
              next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()]
    assert any(e["event"] == "existing_material_reused" for e in events), events


async def test_recreating_everything_never_searches(tmp_path):
    """The other mode: build the tree from the report alone."""
    from camds_imds_importer.camds.import_plan import ImportRequest
    from camds_imds_importer.camds.tree_import import TreeImporter

    searched = []

    class Backend:
        reporter = None

        async def prepare(self): pass
        async def read_back_findings(self): return []
        async def can_reenter_saved(self): return False
        async def saved_children(self, path, at=(0, 1)): return []
        async def find_existing_material(self, node):
            searched.append(node)
            return ("CA_8_9", "6")
        async def find_existing_component(self, node, resolved): return None
        async def create_root(self, node, on_allocated=None, existing=None):
            raise RuntimeError("stop here")

    root = {"uid": "r", "node_type": "COMPONENT", "name": "Part", "weight_g": 1.0,
            "children": [material()]}
    with pytest.raises(RuntimeError, match="stop here"):
        await TreeImporter(Backend(), tmp_path).run(ImportRequest(root), reuse=False)
    assert searched == [], "recreate means recreate"
