"""Finding a Material in CAMDS instead of making another one.

Creating one per run filled the account with duplicates of the same Material.
Reusing needs certainty about identity, and the search rows do not carry a
name - they carry mdsId, symbol and version - so the Material No. is the key
and the name is confirmed afterwards from the saved tree. A duplicate is a
nuisance; the wrong composition attached to a part is wrong data.
"""
import pytest

from camds_imds_importer.camds.api import CamdsApi
from camds_imds_importer.camds.api_backend import ApiBackend


class Camds:
    """Answers findMaterialByCondition and loadMdsTree as CAMDS does."""

    def __init__(self, rows, trees=None):
        self.rows, self.trees, self.asked = rows, trees or {}, []

    async def post(self, url, params=None, data=None, headers=None, timeout=None):
        self.asked.append((url.rsplit("/", 1)[-1], params or {}, data or {}))
        if url.endswith("findMaterialByCondition"):
            body = {"respCode": "0", "ok": True, "data": {"records": self.rows}}
        elif url.endswith("loadMdsTree"):
            body = {"respCode": "0", "ok": True, "data": self.trees.get(params["mdsId"])}
        else:
            body = {"respCode": "0", "ok": True, "data": None}

        class Response:
            status = 200

            @staticmethod
            async def json():
                return body
        return Response


def backend(camds):
    return ApiBackend(CamdsApi(camds, base_url="https://camds.test"))


def material(name="Cu99", number="1274477721"):
    return {"uid": "m", "node_type": "MATERIAL", "name": name, "material_number": number,
            "classification": "1.1.1", "weight_g": 1.0,
            "children": [{"uid": "s", "node_type": "SUBSTANCE", "name": "Copper",
                          "cas_number": "7440-50-8", "percentage": 100, "children": []}]}


def tree(mds_id, text):
    return {"id": "CA_21_1", "mdsId": mds_id, "mdsCver": 1.0, "text": text, "children": []}


async def test_the_newest_released_version_is_reused():
    camds = Camds(
        rows=[{"mdsId": "CA_8_1", "symbol": "1274477721", "version": "1"},
              {"mdsId": "CA_8_9", "symbol": "1274477721", "version": "6"}],
        trees={"CA_8_9": tree("CA_8_9", "Cu99"), "CA_8_1": tree("CA_8_1", "Cu99")})
    assert await backend(camds).find_existing_material(material()) == ("CA_8_9", "6")


async def test_a_draft_version_is_never_reused():
    """0.01 is a half-built draft, possibly one this tool left behind. Attaching
    it would attach an unfinished composition."""
    camds = Camds(rows=[{"mdsId": "CA_8_5", "symbol": "1274477721", "version": "0.01"}],
                  trees={"CA_8_5": tree("CA_8_5", "Cu99")})
    assert await backend(camds).find_existing_material(material()) is None


async def test_a_row_whose_number_only_contains_the_search_is_not_a_match():
    """The search is not exact, so the row is checked rather than trusted."""
    camds = Camds(rows=[{"mdsId": "CA_8_2", "symbol": "1274477721000", "version": "2"}],
                  trees={"CA_8_2": tree("CA_8_2", "Cu99")})
    assert await backend(camds).find_existing_material(material()) is None


async def test_a_different_name_under_the_same_number_is_refused():
    """The rows carry no name, so it is confirmed from the saved tree. Reusing
    on the number alone would attach whatever else shares it."""
    camds = Camds(rows=[{"mdsId": "CA_8_3", "symbol": "1274477721", "version": "2"}],
                  trees={"CA_8_3": tree("CA_8_3", "Something else")})
    assert await backend(camds).find_existing_material(material()) is None


async def test_a_material_with_no_number_is_not_searched_for():
    """A name search answers with rows that have no name in them, so nothing
    could confirm the match."""
    camds = Camds(rows=[{"mdsId": "CA_8_4", "symbol": None, "version": "2"}])
    assert await backend(camds).find_existing_material(material(number=None)) is None
    assert camds.asked == [], "no point asking a question the answer cannot settle"


async def test_the_search_is_by_number_not_by_name():
    camds = Camds(rows=[])
    await backend(camds).find_existing_material(material())
    endpoint, _params, body = camds.asked[0]
    assert endpoint == "findMaterialByCondition"
    assert body["symbol"] == "1274477721"
    assert body["name"] == ""


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
        async def find_existing_component(self, node, resolved):
            return None

        async def find_existing_material(self, node): return ("CA_8_9", "6")
        async def open_saved(self, kind, ref): self.ref = ref
        async def value(self, label): return "Cu99"
        async def identity(self):
            # Read-back selects the attached Material and asks which MDS it is.
            return self.attached if getattr(self, "attached", None) else self.ref
        async def verify_value(self, label, expected): pass
        async def verify_child_count(self, path, count, at=(0, 1)): pass
        async def select(self, path, at=(0, 1)): pass
        async def create_root(self, node, on_allocated=None, existing=None):
            if node["node_type"] == "MATERIAL":
                raise AssertionError("a Material already in CAMDS must not be created again")
            self.ref = ("CA_5_1", "0.01")
            if on_allocated:
                on_allocated(self.ref)
            return self.ref
        async def save(self): pass
        async def add_material(self, path, node, ref, at=(0, 1), by_portion=False, reuse_index=None):
            self.attached = tuple(ref)
            return "Cu99"

    root = {"uid": "r", "node_type": "COMPONENT", "name": "Part", "weight_g": 1.0,
            "children": [material()]}
    importer = TreeImporter(Backend(), tmp_path)
    result = await importer.run(ImportRequest(root))

    assert any("reused CA_8_9/6" in note for note in result["skipped"]), result["skipped"]
    events = [json.loads(line) for line in
              next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8").splitlines()]
    assert any(e["event"] == "existing_material_reused" and e["ref"] == ["CA_8_9", "6"]
               for e in events), events


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
        async def find_existing_component(self, node, resolved):
            return None

        async def find_existing_material(self, node):
            searched.append(node)
            return ("CA_8_9", "6")
        async def create_root(self, node, on_allocated=None, existing=None):
            raise RuntimeError("stop here")

    root = {"uid": "r", "node_type": "COMPONENT", "name": "Part", "weight_g": 1.0,
            "children": [material()]}
    with pytest.raises(RuntimeError, match="stop here"):
        await TreeImporter(Backend(), tmp_path).run(ImportRequest(root), reuse=False)
    assert searched == [], "recreate means recreate"
