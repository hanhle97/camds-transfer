"""The real TreeImporter driven over the API, against a CAMDS stand-in.

The stand-in keeps the shapes the recorded sessions show: nodes addressed by
strutsId, a record under `data`, the parent relation under `structureVO`, and a
save that addresses the root.
"""
import copy
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
    async def post(self, url, params=None, data=None, headers=None, timeout=None):
        params = params or {}
        self.calls.append(url.rsplit("/", 1)[-1])
        return _ok(self._route(url, params, data or {}))

    async def get(self, url, params=None, headers=None, timeout=None):
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
            # loadMdsTree carries the CAS on the tree node itself, which is how
            # a resumed run recognises a substance it already saved.
            self.nodes[sid]["treeDataNode"].update(text=found["enName"], cascode=found["cas"],
                                                   cenName=found["enName"])
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
            # A copy all the way down: a shallow one would let the client mutate
            # the stored node just by loading it, so a missing write would pass.
            return copy.deepcopy(self.nodes[params["strutsId"]])
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


def backend(camds, first_row=True):
    """A backend whose substance choices go to a throwaway file.

    The default SubstanceMapping writes to config/, which a test must never
    touch: a recorded choice would leak from one run into the repository.
    """
    import pathlib
    import tempfile
    from camds_imds_importer.camds.substance_mapping import SubstanceMapping

    mapping = SubstanceMapping(pathlib.Path(tempfile.mkdtemp()) / "substances.json")
    return ApiBackend(CamdsApi(camds, base_url="https://camds.test"), mapping,
                      first_row_when_unclear=first_row)


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
    assert len(called) == 25
    for name in called:
        assert callable(getattr(ApiBackend, name, None)), f"ApiBackend cannot {name}"


async def test_prepare_checks_the_session_before_anything_is_created(tmp_path):
    """A lapsed session must cost nothing: no draft, no journal, no id."""
    class Dead:
        calls = []

        async def post(self, url, params=None, data=None, headers=None, timeout=None):
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
    # Resume reopens that id and finishes it; it never allocates a second one.
    camds._route = original
    before = len([n for n in camds.nodes.values() if n["treeDataNode"]["nodeType"] == 3])
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(material()), resume=True)
    after = [n for n in camds.nodes.values() if n["treeDataNode"]["nodeType"] == 3]
    assert len(after) == before == 1, "resume must not allocate a second Material"
    assert after[0]["data"]["cname"] == "Steel", "the fill the failure interrupted is completed"
    resumed = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text(
        encoding="utf-8").splitlines()]
    assert any(e["event"] == "material_resumed" and e["ref"] == allocated["ref"]
               for e in resumed), resumed


def _vmq():
    camds = FakeCamds(substances=[
        {"csid": "77", "cas": "63148-57-2", "enName": "VMQ (vinyl methyl silicone)"},
        {"csid": "78", "cas": None, "enName": "Silicone rubber"},
    ])
    return camds, material(children=[substance(cas=None, name="VMQ", percentage=None,
                                               percentage_min=10.0, percentage_max=13.0)])


async def test_an_unresolvable_substance_names_what_the_catalogue_offered(tmp_path):
    """With the first-row instruction off, a Material missing part of itself is
    wrong data and the run stops, naming the near misses."""
    camds, root = _vmq()
    with pytest.raises(CamdsApiError) as failure:
        await TreeImporter(backend(camds, first_row=False), tmp_path).run(ImportRequest(root))
    message = str(failure.value)
    assert "matched 0 entries exactly" in message
    # The near misses are named, so a naming difference is visible at a glance.
    assert "VMQ (vinyl methyl silicone)" in message and "63148-57-2" in message
    assert "id 77" in message


async def test_the_first_row_instruction_carries_the_import_and_reports_the_pick(tmp_path):
    """By instruction the run continues. What it chose reaches the operator with
    the result, because nobody approved it."""
    camds, root = _vmq()
    result = await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root))
    picked = [w for w in result["warnings"] if "VMQ" in w]
    assert picked, result["warnings"]
    assert "VMQ (vinyl methyl silicone)" in picked[0] and "id 77" in picked[0]


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

    async def status(url, params=None, headers=None, timeout=None):
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


async def test_resume_adds_only_the_substances_camds_does_not_already_hold(tmp_path):
    """Reconciled against CAMDS, not against the journal.

    A crash between the write and the journal entry would otherwise add a second
    copy of a substance CAMDS already saved.
    """
    camds = FakeCamds()
    original = camds._route
    root = material(children=[substance(uid="s1"),
                              substance(uid="s2", cas="system", name="Misc., not to declare")])

    def refuse_the_second_substance(url, params, body):
        if url.endswith("addNewSubstanceToTree") and params["subId"] == "8172":
            raise CamdsApiError("addNewSubstanceToTree refused: 程序异常")
        return original(url, params, body)

    camds._route = refuse_the_second_substance
    with pytest.raises(CamdsApiError):
        await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root))
    assert len([n for n in camds.nodes.values() if n["treeDataNode"]["nodeType"] == 4]) == 1

    camds._route = original
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root), resume=True)

    saved = [n for n in camds.nodes.values() if n["treeDataNode"]["nodeType"] == 4]
    assert sorted(n["data"]["ccasCode"] for n in saved) == ["7439-89-6", "system"]
    events = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text(
        encoding="utf-8").splitlines()]
    assert [e["name"] for e in events if e["event"] == "substance_already_saved"] == ["Iron"]


async def test_the_browser_backend_still_refuses_to_resume():
    """No flow for re-entering a saved draft has been discovered there."""
    from camds_imds_importer.camds.tree_import import DraftBrowser

    browser = DraftBrowser.__new__(DraftBrowser)
    assert await browser.can_reenter_saved() is False
    with pytest.raises(RuntimeError, match="not been discovered"):
        await browser.create_root({"node_type": "MATERIAL", "name": "Steel"},
                                  existing=("CA_8_1", "0.01"))


async def test_resume_recognises_a_system_group_camds_relabelled(tmp_path):
    """A system group has no CAS, so it is matched on its name - and CAMDS shows
    a saved substance in either language. The English name is what both a
    resumed run and read-back compare, so the Chinese label does not hide it."""
    camds = FakeCamds()
    original = camds._route
    root = material(children=[substance(uid="s1", cas="system", name="Misc., not to declare"),
                              substance(uid="s2")])

    def refuse_iron(url, params, body):
        if url.endswith("addNewSubstanceToTree") and params["subId"] == "2995":
            raise CamdsApiError("addNewSubstanceToTree refused: 程序异常")
        return original(url, params, body)

    camds._route = refuse_iron
    with pytest.raises(CamdsApiError):
        await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root))
    system_node = next(n for n in camds.nodes.values() if n["treeDataNode"]["nodeType"] == 4)
    assert system_node["treeDataNode"]["text"] == "杂质，不需申报", "CAMDS relabelled it"

    camds._route = original
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(root), resume=True)
    saved = [n for n in camds.nodes.values() if n["treeDataNode"]["nodeType"] == 4]
    assert len(saved) == 2, "the relabelled group must not be added a second time"


def _components(camds):
    return [n for n in camds.nodes.values() if n["treeDataNode"]["nodeType"] == 1]


async def test_resume_continues_the_parent_instead_of_spending_a_second_id(tmp_path):
    """The parent Component was allocated; a second run must reopen that MDS."""
    camds = FakeCamds()
    original = camds._route

    def refuse_the_child(url, params, body):
        if url.endswith("addComponentNodeToTree"):
            raise CamdsApiError("addComponentNodeToTree refused: 程序异常")
        return original(url, params, body)

    camds._route = refuse_the_child
    with pytest.raises(CamdsApiError):
        await TreeImporter(backend(camds), tmp_path).run(ImportRequest(tree()))
    roots = [n["treeDataNode"]["mdsId"] for n in _components(camds)]
    assert len(roots) == 1, "only the parent exists so far"

    camds._route = original
    result = await TreeImporter(backend(camds), tmp_path).run(ImportRequest(tree()), resume=True)
    assert result["identity"].split("/")[0] == roots[0], "the same MDS was finished"
    assert len(_components(camds)) == 2, "parent plus the one child, no duplicate parent"


async def test_resume_does_not_add_a_second_copy_of_a_child_already_saved(tmp_path):
    """Failing after the child is written must not duplicate it."""
    camds = FakeCamds()
    original = camds._route

    def refuse_the_material(url, params, body):
        if url.endswith("substituteMdsNode"):
            raise CamdsApiError("substituteMdsNode refused: 程序异常")
        return original(url, params, body)

    camds._route = refuse_the_material
    with pytest.raises(CamdsApiError):
        await TreeImporter(backend(camds), tmp_path).run(ImportRequest(tree()))
    assert len(_components(camds)) == 2, "parent and child were written before the failure"

    camds._route = original
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(tree()), resume=True)
    assert len(_components(camds)) == 2, "the saved child must not be added again"
    events = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text(
        encoding="utf-8").splitlines()]
    assert any(e["event"] == "child_already_saved" and e["name"] == "Child" for e in events)


async def test_resume_fills_a_node_an_interrupted_run_left_unnamed(tmp_path):
    """Created but never named. Skipping it on position alone would leave the
    tree wrong; it is recognised by its name and written again."""
    camds = FakeCamds()
    original = camds._route

    def refuse_naming_the_child(url, params, body):
        if url.endswith("editNodeDate") and (body["view"]["data"] or {}).get("cname") == "Child":
            raise CamdsApiError("editNodeDate refused: 程序异常")
        return original(url, params, body)

    camds._route = refuse_naming_the_child
    with pytest.raises(CamdsApiError):
        await TreeImporter(backend(camds), tmp_path).run(ImportRequest(tree()))
    unnamed = [n for n in _components(camds) if not n["data"].get("cname")]
    assert len(unnamed) == 1, "the child exists but carries no name"

    camds._route = original
    await TreeImporter(backend(camds), tmp_path).run(ImportRequest(tree()), resume=True)
    assert len(_components(camds)) == 2, "no second child beside the unnamed one"
    assert sorted(n["data"]["cname"] for n in _components(camds)) == ["Child", "Parent"]


@pytest.mark.parametrize("value, sent", [
    (6.5e-05, "0.000065"),      # the mass that was stored as 6.5 g
    (0.0000066, "0.0000066"),
    (1e-12, "0.000000000001"),
    (3000, "3000"),             # normalize alone would make this 3E+3
    (2.0, "2"),                 # the recorded quantity is "5", not "5.0"
    (7.98, "7.98"),
    (0.784347, "0.784347"),
    (0.0, "0"),
])
def test_a_number_is_written_the_way_the_browser_writes_it(value, sent):
    """format(x, ".12g") wrote 0.000065 as "6.5e-05". CAMDS read the leading
    6.5 and discarded the exponent, so a mass of 0.065 mg was stored as 6.5 g -
    a hundred thousand times too much, silently, in a declared mass."""
    from camds_imds_importer.camds.api_backend import number

    assert number(value) == sent


def test_no_number_is_ever_written_in_scientific_notation():
    """The whole class of the defect, not just the value that exposed it."""
    from camds_imds_importer.camds.api_backend import number

    for value in (1e-30, 6.5e-05, 1e20, 1.5e16, 0.1 + 0.2):
        assert "e" not in number(value).lower(), (value, number(value))
