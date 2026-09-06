"""The real TreeImporter driven over the API, against a CAMDS stand-in.

The stand-in keeps the shapes the recorded sessions show: nodes addressed by
strutsId, a record under `data`, the parent relation under `structureVO`, and a
save that addresses the root.
"""
import json

import pytest

from camds_imds_importer.camds.api import CamdsApi, CamdsApiError, TreeNode
from camds_imds_importer.camds.api_backend import ApiBackend
from camds_imds_importer.camds.import_plan import ImportRequest
from camds_imds_importer.camds.tree_import import TreeImporter


class FakeCamds:
    """Enough of CAMDS to run a whole import: ids, records and relations."""

    def __init__(self, substances=None, options=None):
        self.counter = 0
        self.nodes = {}          # strutsId -> {"data":…, "structureVO":…, "treeDataNode":…}
        self.children = {}       # strutsId -> [strutsId]
        self.referenced = set()  # strutsIds that point at another MDS
        self.saves = []
        self.applied = []
        self.calls = []
        # A catalogue search row: csid / cas / enName, not the node record's
        # csubId / ccasCode / cenName.
        self.substances = substances if substances is not None else [
            {"csid": "2995", "cas": "7439-89-6", "enName": "Iron", "name": "铁"},
            {"csid": "8172", "cas": "system", "enName": "Misc., not to declare",
             "name": "杂质，不需申报"},
        ]
        self.options = options if options is not None else []

    def _new(self, prefix, node_type, text="", classification=None):
        self.counter += 1
        sid = f"CA_21_{790000 + self.counter}"
        mds = f"{prefix}_{100 + self.counter}"
        record = {"cname": "", "csymbol": "", "cmeaWeightPerItem": 0, "cweightUnit": "g",
                  "ccasCode": None, "cenName": None, "csubId": None, "cratio": None,
                  "cmatClsId": classification}
        relation = {"csid": sid, "cquantity": 1, "cweight": 0, "cweightUnit": "g",
                    "crateType": None, "crate": None, "cminRate": None, "cmaxRate": None}
        self.nodes[sid] = {"data": record, "structureVO": relation,
                           "treeDataNode": {"id": sid, "mdsId": mds, "mdsCver": 0.01,
                                            "text": text, "nodeType": node_type,
                                            "cmatClsId": classification},
                           "materialRecyclateVO": None, "mdsState": "ORIGINAL", "refed": False,
                           "state": "ORIGINAL", "structState": "ORIGINAL", "vocFlag": False}
        self.children[sid] = []
        return sid

    # ------------------------------------------------------- transport surface
    async def post(self, url, params=None, data=None, headers=None):
        params = params or {}
        self.calls.append(url.rsplit("/", 1)[-1])
        return _ok(self._route(url, params, data or {}))

    async def get(self, url, params=None, headers=None):
        return _ok(None)

    def _route(self, url, params, body):
        if url.endswith("createInitComponent"):
            return {"treeDataNode": self.nodes[self._new("CA_5", 1)]["treeDataNode"]}
        if url.endswith("createInitMaterial"):
            sid = self._new("CA_8", 3, classification=params["classification"])
            return {"treeDataNode": self.nodes[sid]["treeDataNode"]}
        if url.endswith("addComponentNodeToTree"):
            return {"treeDataNode": self._attach(params["parentid"], self._new("CA_5", 1))}
        if url.endswith("addNewSemiComponentToTree"):
            return {"treeDataNode": self._attach(params["parentid"], self._new("CA_7", 2))}
        if url.endswith("addNewSubstanceToTree"):
            sid = self._new("SUB", 4)
            found = next(s for s in self.substances if s["csid"] == params["subId"])
            # Attaching turns a search row into a node record, renaming as CAMDS does.
            self.nodes[sid]["data"].update({"csubId": found["csid"], "ccasCode": found["cas"],
                                            "cenName": found["enName"], "cname": found.get("name")})
            self.nodes[sid]["treeDataNode"]["text"] = found["enName"]
            return {"treeDataNode": self._attach(params["parentStrutsId"], sid)}
        if url.endswith("substituteMdsNode"):
            sid = self._new("REF", 3, text="Steel")
            self.nodes[sid]["data"]["cname"] = "Steel"
            self.nodes[sid]["treeDataNode"].update(mdsId=params["mdsId"], mdsCver=0.01)
            self.referenced.add(sid)
            return self._attach(params["parentStrutsId"], sid)
        if url.endswith("loadNodeDate"):
            # CAMDS returns a referenced node under a prefixed tree id but only
            # ever accepts the bare one; the prefixed id is refused.
            if params["strutsId"] not in self.nodes:
                raise AssertionError(f"loadNodeDate refused {params['strutsId']}")
            return dict(self.nodes[params["strutsId"]])
        if url.endswith("editNodeDate"):
            sid = body["editedStructId"]
            for key in ("data", "structureVO"):
                self.nodes[sid][key] = dict(body["view"][key])
            label = self.nodes[sid]["data"].get("cname") or self.nodes[sid]["data"].get("cenName")
            if label:
                self.nodes[sid]["treeDataNode"]["text"] = label
            return None
        if url.endswith("saveNodeDate"):
            self.saves.append((params["StrutsId"], params["mdsId"]))
            return None
        if url.endswith("loadMdsTree"):
            sid = next(s for s, n in self.nodes.items()
                       if n["treeDataNode"]["mdsId"] == params["mdsId"])
            return self._tree(sid)
        if url.endswith("findSubstanceByCondition"):
            cas, name = body.get("cas", ""), body.get("name", "")
            # A real catalogue search matches loosely; the client is what
            # insists on one exact hit.
            rows = [s for s in self.substances
                    if (cas and cas in str(s.get("cas") or ""))
                    or (name and name.casefold() in str(s.get("enName") or "").casefold())]
            return {"records": rows, "total": len(rows)}
        if url.endswith("getApplyAppstd"):
            return {"selectedOption": "", "applyViewList": self.options}
        if url.endswith("addOrUpdateApply"):
            self.applied.append(body)
            return None
        return None

    def _attach(self, parent, sid):
        self.children[parent].append(sid)
        node = self.nodes[sid]
        node["structureVO"]["cpsid"] = parent
        return {"treeDataNode": node["treeDataNode"]} if False else node["treeDataNode"]

    def _tree(self, sid):
        """As loadMdsTree answers: a referenced node under a "ref1_-" id."""
        node = dict(self.nodes[sid]["treeDataNode"])
        if sid in self.referenced:
            node["id"] = "ref1_-" + sid
            node["crefFlag"], node["rf"] = "1", True
        node["children"] = [self._tree(child) for child in self.children[sid]]
        return node


def _ok(data):
    class Response:
        status = 200

        @staticmethod
        async def json():
            return {"respCode": "0", "data": data, "ok": True, "message": ""}
    return Response


def backend(camds):
    return ApiBackend(CamdsApi(camds, base_url="https://camds.test"))


def substance(uid="s", cas="7439-89-6", name="Iron", **extra):
    return dict({"uid": uid, "node_type": "SUBSTANCE", "name": name, "cas_number": cas,
                 "percentage": 100, "children": []}, **extra)


def material(uid="m", mass=5.0, **extra):
    return dict({"uid": uid, "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1",
                 "weight_g": mass, "material_number": "M1", "children": [substance()]}, **extra)


def tree():
    child = {"uid": "c", "node_type": "COMPONENT", "name": "Child", "part_number": "C1",
             "weight_g": 5.0, "quantity": 2, "children": [material()]}
    return {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "part_number": "P1",
            "weight_g": 10.0, "children": [child]}


async def test_a_whole_tree_imports_over_the_api_and_reads_back(tmp_path):
    camds = FakeCamds()
    result = await TreeImporter(backend(camds), tmp_path).run(ImportRequest(tree()))
    assert result["identity"].startswith("CA_5_")
    assert result["nodes"] == result["total"]
    # One Material with its Substance, then the Component tree.
    assert camds.calls.count("createInitMaterial") == 1
    assert camds.calls.count("createInitComponent") == 1
    assert camds.calls.count("addNewSubstanceToTree") == 1
    assert camds.calls.count("addComponentNodeToTree") == 1
    assert camds.calls.count("substituteMdsNode") == 1
    # Read-back is one call per saved MDS, not a walk through a rendered tree.
    assert camds.calls.count("loadMdsTree") == 2


async def test_a_material_is_created_in_its_own_classification(tmp_path):
    camds = FakeCamds()
    root = material(uid="m", classification="7.2: Ceramics / glass")
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root))
    created = next(n for n in camds.nodes.values() if n["treeDataNode"]["nodeType"] == 3)
    assert created["treeDataNode"]["cmatClsId"] == "7.2"


async def test_quantity_and_mass_are_written_to_the_relation(tmp_path):
    camds = FakeCamds()
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(tree()))
    child = next(n for n in camds.nodes.values()
                 if n["data"].get("cname") == "Child")
    # CAMDS receives numbers typed into a form as strings.
    assert child["structureVO"]["cquantity"] == "2"
    assert child["data"]["cmeaWeightPerItem"] == "5"
    reference = next(n for n in camds.nodes.values()
                     if n["treeDataNode"]["nodeType"] == 3 and n["structureVO"].get("cweight"))
    assert reference["structureVO"]["cweight"] == "5"
    assert reference["structureVO"]["cweightUnit"] == "g"


@pytest.mark.parametrize("portion_fields, expected", [
    ({"percentage": 100}, {"crateType": 2, "crate": 100.0}),
    ({"percentage": None, "percentage_min": 40.0, "percentage_max": 60.0},
     {"crateType": 1, "cminRate": 40.0, "cmaxRate": 60.0}),
    ({"percentage": None, "is_rest": True}, {"crateType": 3, "crate": 0}),
])
async def test_every_portion_mode_reaches_the_relation(tmp_path, portion_fields, expected):
    camds = FakeCamds()
    root = material(children=[substance(**portion_fields)])
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root))
    added = next(n for n in camds.nodes.values() if n["data"].get("csubId") == "2995")
    for field, value in expected.items():
        assert added["structureVO"][field] == value


async def test_a_substance_without_a_cas_is_found_by_name(tmp_path):
    camds = FakeCamds()
    root = material(children=[substance(cas="system", name="Misc., not to declare")])
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root))
    added = next(n for n in camds.nodes.values() if n["data"].get("csubId") == "8172")
    assert added["data"]["ccasCode"] == "system"


async def test_a_substance_that_is_not_in_the_catalogue_stops_the_run(tmp_path):
    camds = FakeCamds(substances=[])
    root = material(children=[substance()])
    with pytest.raises(CamdsApiError, match="matched 0 entries exactly"):
        await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root))


async def test_saving_always_addresses_the_root(tmp_path):
    camds = FakeCamds()
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(tree()))
    roots = {mds for _, mds in camds.saves}
    # Two roots exist: the Material and the parent Component. No child is saved.
    assert all(mds.startswith(("CA_8_", "CA_5_")) for mds in roots)
    for struts, mds in camds.saves:
        assert camds.nodes[struts]["treeDataNode"]["mdsId"] == mds


async def test_a_read_back_mismatch_is_reported(tmp_path, monkeypatch):
    camds = FakeCamds()
    api_backend = backend(camds)
    original = api_backend.verify_value

    async def wrong(label, expected):
        if label == "Article Name":
            expected = "Something else"
        return await original(label, expected)

    monkeypatch.setattr(api_backend, "verify_value", wrong)
    with pytest.raises(CamdsApiError, match="Read-back mismatch"):
        await TreeImporter(api_backend, tmp_path).run(ImportRequest(tree()))


async def test_an_unmatched_application_is_skipped_not_guessed(tmp_path):
    camds = FakeCamds(options=[{"enOption": "Something unrelated", "optionCode": "9",
                                "appstdid": "99"}])
    root = material(children=[substance(application_text="Not applicable [34]",
                                        application_id="34")])
    result = await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root))
    assert camds.applied == [], "an unclear application must not be written"
    assert result["skipped"] and "left unset" in result["skipped"][0]


async def test_a_matching_application_is_written_with_both_identifiers(tmp_path):
    camds = FakeCamds(options=[{"enOption": "Not applicable", "optionCode": "39",
                                "appstdid": "1014"}])
    root = material(children=[substance(application_text="Not applicable [34]",
                                        application_id="34")])
    result = await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root))
    assert not result["skipped"]
    written = camds.applied[0]
    # The CAMDS option code, never the IMDS id.
    assert written["optionCode"] == "39" and written["appstdid"] == "1014"
    assert written["subId"] == "2995" and written["enName"] == "Iron"


def test_the_worker_uses_the_api_unless_it_is_turned_off(monkeypatch):
    from pathlib import Path
    from camds_imds_importer.workers.operations_worker import OperationsWorker
    monkeypatch.delenv("CAMDS_USE_API", raising=False)
    assert OperationsWorker(Path("x")).use_api is True
    monkeypatch.setenv("CAMDS_USE_API", "0")
    assert OperationsWorker(Path("x")).use_api is False


async def test_the_api_backend_answers_every_call_the_importer_makes():
    """A missing method would surface mid-import, after objects were saved."""
    import inspect
    import re
    from camds_imds_importer.camds import tree_import
    source = inspect.getsource(tree_import)
    called = sorted(set(re.findall(r"self\.io\.(\w+)", source)))
    assert len(called) == 19
    for name in called:
        assert callable(getattr(ApiBackend, name, None)), f"ApiBackend cannot {name}"


async def test_prepare_checks_the_session_before_anything_is_created(tmp_path):
    """A lapsed session must cost nothing: no draft, no journal, no id."""
    class Dead:
        calls = []

        async def post(self, url, params=None, data=None, headers=None):
            Dead.calls.append(url)

            class Response:
                status = 200

                @staticmethod
                async def json():
                    raise ValueError("<html>login</html>")
            return Response
        get = post

    with pytest.raises(CamdsApiError, match="refused a read before anything was created"):
        await TreeImporter(backend(Dead()), tmp_path).run(ImportRequest(tree()))
    assert not list(tmp_path.iterdir()), "no journal may be written"
    assert len(Dead.calls) == 1, "it must stop at the first read, not keep going"


async def test_a_working_session_passes_the_check_and_creates_nothing_yet():
    camds = FakeCamds()
    await backend(camds).prepare()
    assert camds.nodes == {}
    assert camds.calls == ["findMaterialByCondition"]


async def test_the_client_awaits_the_body_like_playwright_does():
    """Playwright's APIResponse.json() is a coroutine; a sync call silently
    yields a coroutine object instead of the payload."""
    camds = FakeCamds()
    node = await CamdsApi(camds, base_url="https://camds.test").create_component_root()
    assert isinstance(node.struts_id, str) and node.struts_id.startswith("CA_21_")


async def test_an_id_is_journalled_before_the_node_is_filled(tmp_path):
    """CAMDS spends the id at creation; a failure while filling must still name it."""
    camds = FakeCamds()
    original = camds._route

    def refuse_the_fill(url, params, body):
        if url.endswith("editNodeDate"):
            raise CamdsApiError("editNodeDate refused: 程序异常")
        return original(url, params, body)

    camds._route = refuse_the_fill
    with pytest.raises(CamdsApiError, match="程序异常"):
        await TreeImporter(backend(camds), tmp_path).run(ImportRequest(material()))

    events = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text(
        encoding="utf-8").splitlines()]
    allocated = next(e for e in events if e["event"] == "material_id_allocated")
    assert allocated["ref"][0].startswith("CA_8_"), "the journal must name the spent id"
    assert events[-1]["event"] == "interrupted_or_failed"
    # And resume must refuse, naming that id rather than creating a second one.
    retry = FakeCamds()
    with pytest.raises(RuntimeError, match="created but never verified"):
        await TreeImporter(backend(retry), tmp_path).run(ImportRequest(material()), resume=True)
    assert retry.nodes == {}


async def test_an_unresolvable_substance_names_what_the_catalogue_offered(tmp_path):
    """A Material missing part of itself is wrong data, so this stops the run."""
    camds = FakeCamds(substances=[
        {"csid": "77", "cas": "63148-57-2", "enName": "VMQ (vinyl methyl silicone)"},
        {"csid": "78", "cas": None, "enName": "Silicone rubber"},
    ])
    root = material(children=[substance(cas=None, name="VMQ", percentage=None,
                                        percentage_min=10.0, percentage_max=13.0)])
    with pytest.raises(CamdsApiError) as failure:
        await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root))
    message = str(failure.value)
    assert "matched 0 entries exactly" in message
    # The near misses are named, so a naming difference is visible at a glance.
    assert "VMQ (vinyl methyl silicone)" in message and "63148-57-2" in message
    assert "id 77" in message


async def test_an_empty_catalogue_answer_says_so_plainly(tmp_path):
    camds = FakeCamds(substances=[])
    root = material(children=[substance(cas=None, name="VMQ")])
    with pytest.raises(CamdsApiError, match="no rows at all"):
        await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root))


async def test_reading_a_saved_mds_announces_it_before_asking_for_the_tree(tmp_path):
    """CAMDS serves the tree but refuses its nodes unless the MDS was announced
    with getMdsStatus / getMaterialStatus first."""
    camds = FakeCamds()
    order = []
    original = camds._route

    def note(url, params, body):
        if url.endswith("loadMdsTree"):
            order.append(("tree", params["mdsId"]))
        return original(url, params, body)

    async def status(url, params=None, headers=None):
        order.append(("status", url.rsplit("/", 1)[-1]))
        return _ok(None)

    camds._route = note
    camds.get = status
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(tree()))

    assert order, "the saved MDS must be announced and then read"
    # Every tree read is preceded by a status call for the same MDS.
    for index, (kind, value) in enumerate(order):
        if kind == "tree":
            assert index > 0 and order[index - 1][0] == "status", order
            assert order[index - 1][1] == value, order


async def test_a_referenced_material_is_asked_about_before_it_is_read(tmp_path):
    """The recordings call canbeModifyMx before loading a referenced MDS node."""
    camds = FakeCamds()
    asked = []
    original = camds._route

    def watch(url, params, body):
        if url.endswith("canbeModifyMx"):
            asked.append(params["mdsId"])
        return original(url, params, body)

    camds._route = watch
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(tree()))
    assert asked, "a referenced Material must be announced before it is read"
    assert all(mds.startswith("CA_8_") for mds in asked)


def _rest_backend(saved_rate):
    """A backend positioned on a saved Rest substance CAMDS resolved itself."""
    from camds_imds_importer.camds.api_backend import ApiBackend

    made = ApiBackend(FakeCamds())
    made.view = {"data": {}, "structureVO": {"crateType": 3, "crate": saved_rate,
                                             "cminRate": 0, "cmaxRate": 0}}
    return made


async def test_a_saved_rest_is_verified_as_a_mode_not_as_a_number():
    """IMDS prints "Rest 7.98"; CAMDS computes the remainder and stores 7.98.

    We write Rest with no value, so demanding our 0 back fails a portion CAMDS
    saved correctly. This ended a live run on 2026-09-06.
    """
    made = _rest_backend(7.98)
    await made.verify_proportion({"name": "VMQ", "is_rest": True, "percentage": 7.98})
    assert made.findings == []


async def test_a_remainder_camds_computes_differently_is_reported_not_refused():
    made = _rest_backend(9.5)
    await made.verify_proportion({"name": "VMQ", "is_rest": True, "percentage": 7.98})
    assert len(made.findings) == 1
    assert "7.98" in made.findings[0] and "9.5" in made.findings[0]


async def test_a_saved_portion_of_the_wrong_mode_still_fails():
    """Rest and Fixed are different declarations, whatever the numbers say."""
    from camds_imds_importer.camds.api import CamdsApiError

    made = _rest_backend(7.98)
    made.view["structureVO"]["crateType"] = 2
    with pytest.raises(CamdsApiError, match="portion mode"):
        await made.verify_proportion({"name": "VMQ", "is_rest": True, "percentage": 7.98})


async def test_a_fixed_portion_is_still_checked_by_value():
    from camds_imds_importer.camds.api import CamdsApiError

    made = _rest_backend(7.98)
    made.view["structureVO"] = {"crateType": 2, "crate": 40.0, "cminRate": 0, "cmaxRate": 0}
    with pytest.raises(CamdsApiError, match="proportion mismatch"):
        await made.verify_proportion({"name": "VMQ", "percentage": 50.0})
