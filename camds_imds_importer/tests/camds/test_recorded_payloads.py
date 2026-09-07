"""Replay recorded CAMDS traffic through the client.

Hand-written fakes reproduce what the author understood, so they cannot catch
what the author misunderstood. Three defects reached production that way: an
unawaited body, missing `Origin`/`Referer`, and a null `cindex` and
`recycledmaterials` that CAMDS answers with a generic "程序异常".

These cases come from `recorded/camds_payloads.json`, lifted from real sessions.
The rule is simple: given what CAMDS answered, the client must send what the
browser sent.
"""
import json
from pathlib import Path

import pytest

from camds_imds_importer.camds.api import CamdsApi

RECORDED = json.loads(
    (Path(__file__).parent / "recorded" / "camds_payloads.json").read_text(encoding="utf-8"))
CASES = {case["label"]: case for case in RECORDED["cases"]}


class Replay:
    """Answers loadNodeDate from the recording and captures what we send."""

    def __init__(self, loaded):
        self.loaded = loaded
        self.sent = None

    async def post(self, url, params=None, data=None, headers=None, timeout=None):
        self.headers = headers
        if url.endswith("/loadNodeDate"):
            body = self.loaded
        else:
            self.sent = data
            body = {"respCode": "0", "data": None, "ok": True}

        class Response:
            status = 200

            @staticmethod
            async def json():
                return body
        return Response


@pytest.mark.parametrize("load_label, edit_label, change", [
    ("material root load", "material root edit", {"cname": "Cu99"}),
    ("component root load", "component root edit",
     {"cname": "U557E MY26 PHEV BATTERY  ASM-. 12V Powernet", "csymbol": "044200509K",
      "cmeaWeightPerItem": "3000"}),
])
async def test_the_client_sends_what_the_browser_sent(load_label, edit_label, change):
    loaded, recorded = CASES[load_label], CASES[edit_label]
    transport = Replay(loaded["response"])
    api = CamdsApi(transport)
    await api.set_fields(recorded["request"]["editedStructId"], change)

    ours, theirs = transport.sent, recorded["request"]
    assert set(ours) == set(theirs), "the request envelope must match"
    assert ours["editedStructId"] == theirs["editedStructId"]
    assert ours["parentId"] == theirs["parentId"]
    assert ours["brotherSidList"] == theirs["brotherSidList"]
    for section in ("data", "structureVO"):
        mine = ours["view"].get(section) or {}
        recorded_section = theirs["view"].get(section) or {}
        assert set(mine) == set(recorded_section), f"view.{section} field names differ"
        differing = {k: (recorded_section[k], mine[k]) for k in recorded_section
                     if recorded_section[k] != mine[k]}
        assert not differing, f"view.{section} values differ: {differing}"
    for key in ("materialRecyclateVO", "mdsState", "refed", "state", "structState", "vocFlag"):
        assert ours["view"][key] == theirs["view"][key], key


async def test_a_material_write_never_carries_a_null_recyclate():
    """CAMDS refuses it with a generic program exception."""
    loaded = CASES["material root load"]["response"]
    assert (loaded["data"]["structureVO"]["recycledmaterials"] is None
            and loaded["data"]["structureVO"]["cindex"] is None), "the recording must still show the nulls"
    transport = Replay(loaded)
    await CamdsApi(transport).set_fields("CA_21_791936236", {"cname": "Cu99"})
    relation = transport.sent["view"]["structureVO"]
    assert relation["recycledmaterials"] is not None
    assert relation["cindex"] is not None


async def test_same_origin_headers_travel_with_every_write():
    """Without them CAMDS answers a real path with HTTP 404."""
    transport = Replay(CASES["material root load"]["response"])
    await CamdsApi(transport).set_fields("CA_21_791936236", {"cname": "Cu99"})
    assert transport.headers["Origin"] == "https://catarc.camds.org.cn"
    assert transport.headers["Referer"] == "https://catarc.camds.org.cn/"


@pytest.mark.parametrize("label", [c["label"] for c in RECORDED["cases"] if c["kind"] == "portion"])
def test_recorded_portions_use_the_modes_the_client_writes(label):
    from camds_imds_importer.camds.api import FIXED, FROM_TO, REST, portion
    relation = CASES[label]["structureVO"]
    mode = relation["crateType"]
    assert mode in (FROM_TO, FIXED, REST)
    if mode == FROM_TO:
        expected = portion(FROM_TO, float(relation["cminRate"]), float(relation["cmaxRate"]))
        assert float(relation["cminRate"]) == expected["cminRate"]
    elif mode == FIXED:
        assert portion(FIXED, relation["crate"])["crate"] == relation["crate"]
    else:
        assert portion(REST)["crate"] == 0


def test_a_material_relation_always_carries_a_recyclate_and_a_position():
    """The invariant the recordings show, stated once."""
    for case in RECORDED["cases"]:
        relation = (case.get("structureVO")
                    or ((case.get("request") or {}).get("view") or {}).get("structureVO"))
        if not relation or case["kind"] == "load":
            continue
        assert relation["cindex"] is not None, case["label"]
        if relation.get("cnodeType") == 3:
            assert relation["recycledmaterials"] is not None, case["label"]


SEARCHES = [c for c in RECORDED["cases"] if c["kind"] == "substance_search"]


@pytest.mark.parametrize("case", SEARCHES, ids=lambda c: c["label"])
async def test_a_recorded_search_row_is_read_with_the_right_field_names(case):
    """A search row is csid/cas/enName; a node record is csubId/ccasCode/cenName.
    Reading one with the other's names makes every field silently missing."""
    from camds_imds_importer.camds.api import SEARCH_CAS, SEARCH_ID, SEARCH_NAME

    class Search:
        async def post(self, url, params=None, data=None, headers=None, timeout=None):
            class Response:
                status = 200

                @staticmethod
                async def json():
                    return case["response"]
            return Response

    rows = await CamdsApi(Search()).find_substance(name="x")
    assert rows, "the recording holds at least one row"
    for row in rows:
        assert row.get(SEARCH_ID), f"no {SEARCH_ID} in {sorted(row)}"
        assert row.get(SEARCH_NAME), f"no {SEARCH_NAME} in {sorted(row)}"
        assert SEARCH_CAS in row


async def test_a_stray_space_is_trimmed_before_searching():
    """CAMDS does not trim: ' 7440-50-8' was recorded returning nothing."""
    sent = {}

    class Capture:
        async def post(self, url, params=None, data=None, headers=None, timeout=None):
            sent.update(data)

            class Response:
                status = 200

                @staticmethod
                async def json():
                    return {"respCode": "0", "data": {"records": []}, "ok": True}
            return Response

    await CamdsApi(Capture()).find_substance(cas=" 7440-50-8 ", name="  ")
    assert sent["cas"] == "7440-50-8" and sent["name"] == ""


RELATIONS = [c for c in RECORDED["cases"] if c["kind"] == "relation"]


@pytest.mark.parametrize("case", RELATIONS, ids=lambda c: c["label"])
async def test_a_relation_write_matches_the_recorded_one(case):
    """Mass and quantity travel as strings, and a child edit names its parent
    and its siblings. Sending numbers, or omitting either, is answered with a
    generic "程序异常"."""
    recorded = case["request"]
    relation = recorded["view"]["structureVO"]
    change = ({"cweight": relation["cweight"], "cweightUnit": relation["cweightUnit"]}
              if relation.get("cweight") is not None else {"cquantity": relation["cquantity"]})
    # The values the browser sent are strings, not numbers.
    assert all(isinstance(v, str) for v in change.values()), change

    transport = Replay(case["loaded"])
    await CamdsApi(transport).set_relation(
        recorded["editedStructId"], change, parent_id=recorded["parentId"],
        brothers=tuple(recorded["brotherSidList"]))

    ours = transport.sent
    assert ours["parentId"] == recorded["parentId"]
    assert ours["brotherSidList"] == recorded["brotherSidList"]
    mine, theirs = ours["view"]["structureVO"], relation
    differing = {k: (theirs.get(k), mine.get(k)) for k in set(theirs) | set(mine)
                 if theirs.get(k) != mine.get(k)}
    assert not differing, f"structureVO differs: {differing}"


SAVED_TREE = CASES["saved component tree with a referenced material"]


class RecordingApi:
    """Every call CAMDS receives, in order, answered as the recording does."""

    def __init__(self, tree):
        self.tree = tree
        self.calls = []

    async def mds_status(self, mds_id):
        self.calls.append(("mds_status", mds_id))

    async def material_status(self, mds_id):
        self.calls.append(("material_status", mds_id))

    async def load_tree(self, mds_id):
        self.calls.append(("load_tree", mds_id))
        return self.tree

    async def can_modify(self, mds_id):
        self.calls.append(("can_modify", mds_id))

    async def is_standard_material(self, mds_id):
        self.calls.append(("is_standard_material", mds_id))

    async def load_view(self, struts_id):
        self.calls.append(("load_view", struts_id))
        return {"data": {}, "structureVO": {}, "treeDataNode": {}}


async def test_a_referenced_node_is_addressed_without_its_tree_prefix():
    """loadMdsTree returns "ref1_-CA_21_612736365"; every call takes the bare id.

    Posting the prefixed one is answered with a generic "程序异常", which is what
    ended the first component read-back.
    """
    from camds_imds_importer.camds.api_backend import ApiBackend

    api = RecordingApi(SAVED_TREE["response"])
    backend = ApiBackend(api)
    await backend.open_saved("Component", (SAVED_TREE["mds_id"], "0.01"))
    await backend.select(("METAL-FILM RESISTOR", "Aluminium alloys", "Aluminium Wire"))

    loaded = [sid for name, sid in api.calls if name == "load_view"]
    assert all(not sid.startswith("ref") for sid in loaded), loaded
    assert "CA_21_612736365" in loaded


async def test_reading_a_referenced_material_asks_what_the_browser_asks():
    """canbeModifyMx before the node, isStandMaterial before its substances."""
    from camds_imds_importer.camds.api_backend import ApiBackend

    api = RecordingApi(SAVED_TREE["response"])
    backend = ApiBackend(api)
    await backend.open_saved("Component", (SAVED_TREE["mds_id"], "0.01"))
    await backend.select(("METAL-FILM RESISTOR", "Aluminium alloys", "Aluminium Wire"))

    assert api.calls[:2] == [("mds_status", "CA_5_124767559"),
                             ("load_tree", "CA_5_124767559")]
    assert api.calls[-3:] == [("can_modify", "CA_8_34231714"),
                              ("load_view", "CA_21_612736365"),
                              ("is_standard_material", "CA_8_34231714")]


async def test_an_unreferenced_node_is_not_asked_about():
    """A node the MDS owns needs neither call; sending them is noise."""
    from camds_imds_importer.camds.api_backend import ApiBackend

    api = RecordingApi(SAVED_TREE["response"])
    backend = ApiBackend(api)
    await backend.open_saved("Component", (SAVED_TREE["mds_id"], "0.01"))
    await backend.select(("METAL-FILM RESISTOR", "Aluminium alloys"))
    assert ("can_modify", "CA_5_124767560") not in api.calls


def _case(label):
    return CASES[label]


async def test_the_recyclate_request_matches_the_recorded_one_field_for_field():
    """Three runs were spent on this call. The whole record was right; the
    body around it was missing structId, which addresses the node the form had
    loaded. Comparing the summary rather than the request is what hid it."""
    from camds_imds_importer.camds.api import RECYCLATE_NONE, CamdsApi

    recorded = _case("release: recyclate answered No")["request"]
    sent = {}

    class Capture:
        async def post(self, url, params=None, data=None, headers=None, timeout=None):
            sent.update(data)

            class Response:
                status = 200

                @staticmethod
                async def json():
                    return {"respCode": "0", "ok": True, "data": None}
            return Response

    await CamdsApi(Capture()).set_material_recyclate(
        recorded["mdsId"], recorded["structId"], dict(RECYCLATE_NONE))

    assert {k: v for k, v in sent.items() if k != "_t"} ==            {k: v for k, v in recorded.items() if k != "_t"}


def test_the_recyclate_answer_is_the_whole_record_the_browser_sent():
    """Sending only the five fields that carry a value was answered with the
    generic "程序异常", the same way a null cindex was. CAMDS wants the whole
    record, not the difference."""
    from camds_imds_importer.camds.api import RECYCLATE_NONE

    recorded = _case("release: recyclate answered No")["request"]["materialRecyclateVO"]
    assert RECYCLATE_NONE == recorded
    assert len(RECYCLATE_NONE) == 29
    assert RECYCLATE_NONE["containRecyclate"] == 2, "2 is No"


def test_validation_is_the_gate_the_recording_shows_it_to_be():
    """One error before the recyclate answer, none after. Publishing on the
    first of those would have released an incomplete MDS."""
    answers = _case("release: validate before and after")["answers"]
    assert answers[0]["errorSize"] == 1 and answers[0]["errorFlag"] == "1"
    assert answers[-1]["errorSize"] == 0 and answers[-1]["errorFlag"] == "0"


def test_publishing_addresses_the_mds_in_the_query_and_carries_nothing_else():
    recorded = _case("release: innerPublish")
    assert recorded["query"].startswith("mdsId=CA_8_")
    assert set(recorded["request"]) == {"_t"}
    assert recorded["response"]["respCode"] == "0"
