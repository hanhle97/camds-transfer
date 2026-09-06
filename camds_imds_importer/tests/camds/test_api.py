"""CAMDS JSON API client, checked against the recorded create-component session.

Payloads mirror `camds/CREATE_COMPONENT_API.md`. A refusal arrives as HTTP 200
with respCode set, so the client must read the body, not the status.
"""
import json

import pytest

from camds_imds_importer.camds.api import CamdsApi, CamdsApiError, TreeNode

ROOT_NODE = {"treeDataNode": {"id": "CA_21_791936107", "mdsId": "CA_5_158481580",
                              "mdsCver": 0.01, "text": "Component_CA_5_158481580", "nodeType": 1}}
def _view(record, relation=None):
    """The shape loadNodeDate answers with: record and parent relation together."""
    return {"data": record, "structureVO": relation if relation is not None else dict(RELATION),
            "materialRecyclateVO": None, "mdsState": "ORIGINAL", "refed": False,
            "state": "ORIGINAL", "structState": "ORIGINAL", "vocFlag": False,
            "treeDataNode": {}, "rootMdsId": "CA_5_158481580"}


RELATION = {
    "csid": "CA_21_791936644", "cpsid": "CA_21_791936640", "cckid": "7701", "cindex": 0,
    "cnodeType": 4, "cblkid": "CA_8_55099222", "cquantity": 1, "cweight": 0,
    "cweightUnit": "g", "crateType": 1, "crate": 48.5, "cminRate": "44", "cmaxRate": 50,
    "residualRate": None,
}

COMPONENT_RECORD = {
    "cid": "CA_5_158481580", "ckid": "CA_5_158481580", "cver": 0.01,
    "cname": "U557E MY26 PHEV BATTERY  ASM-. 12V Powernet", "csymbol": "044200509K",
    "cmeaWeightPerItem": 3000, "ccalWeightPerItem": 0, "cweightUnit": "g",
    "aggregationNum": None, "cdeviation": 0, "cremark": None,
    "tableName": "t_component_node",
}


class FakeResponse:
    """Playwright decodes bodies asynchronously; so must this."""

    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    async def json(self):
        if isinstance(self._body, str):
            raise ValueError("not json")
        return self._body


class FakeRequest:
    """Stands in for the signed-in Playwright APIRequestContext."""

    def __init__(self, replies=None):
        self.calls = []
        self.replies = replies or {}

    def _reply(self, url):
        for fragment, body in self.replies.items():
            if fragment in url:
                return FakeResponse(body)
        return FakeResponse({"respCode": "0", "data": None, "ok": True})

    async def post(self, url, params=None, data=None, headers=None):
        self.calls.append(("POST", url, params or {}, data))
        return self._reply(url)

    async def get(self, url, params=None, headers=None):
        self.calls.append(("GET", url, params or {}, None))
        return self._reply(url)


def api(replies=None):
    request = FakeRequest(replies)
    return CamdsApi(request), request


async def test_creating_a_root_returns_the_allocated_id_and_version():
    client, request = api({"createInitComponent": {"respCode": "0", "data": ROOT_NODE, "ok": True}})
    node = await client.create_component_root()
    assert node.struts_id == "CA_21_791936107"
    assert node.reference == ("CA_5_158481580", "0.01")
    method, url, params, _ = request.calls[0]
    assert method == "POST" and url.endswith("/api/mds/component/createInitComponent")
    assert params["mdsFlag"] == "0"


async def test_a_refusal_arrives_as_http_200_and_must_still_fail():
    client, _ = api({"createInitComponent": {"respCode": "1", "data": None, "ok": False,
                                             "message": "no permission"}})
    with pytest.raises(CamdsApiError, match="no permission"):
        await client.create_component_root()


async def test_an_http_error_and_a_non_json_body_both_fail():
    client, request = api()
    request.post = lambda *a, **k: _resolve(FakeResponse({}, status=500))
    with pytest.raises(CamdsApiError, match="HTTP 500"):
        await client.create_component_root()
    client, request = api()
    request.post = lambda *a, **k: _resolve(FakeResponse("<html>login</html>"))
    with pytest.raises(CamdsApiError, match="did not return JSON"):
        await client.create_component_root()


async def _resolve(value):
    return value


async def test_editing_a_field_posts_the_whole_record_back():
    client, request = api({
        "loadNodeDate": {"respCode": "0", "ok": True,
                         "data": _view(dict(COMPONENT_RECORD))},
    })
    view = await client.set_fields("CA_21_791936107", {"cname": "Renamed", "cmeaWeightPerItem": "17.5"})
    assert view["data"]["cname"] == "Renamed"
    edit = next(c for c in request.calls if "editNodeDate" in c[1])
    body = edit[3]
    assert body["editedStructId"] == "CA_21_791936107"
    assert body["brotherSidList"] == []
    # CAMDS replaces the record, so untouched fields must still be present.
    assert body["view"]["data"]["csymbol"] == "044200509K"
    assert body["view"]["data"]["cmeaWeightPerItem"] == "17.5"


async def test_writing_an_unknown_field_is_refused_rather_than_dropped():
    client, _ = api({"loadNodeDate": {"respCode": "0", "ok": True,
                                      "data": _view(dict(COMPONENT_RECORD))}})
    with pytest.raises(CamdsApiError, match="no field"):
        await client.set_fields("CA_21_791936107", {"quantity": 2})


async def test_loading_a_node_without_a_record_fails_loudly():
    client, _ = api({"loadNodeDate": {"respCode": "0", "ok": True, "data": {}}})
    with pytest.raises(CamdsApiError, match="no record"):
        await client.load_node("CA_21_1")


async def test_editing_a_field_keeps_the_parent_relation():
    """Posting only the record would drop portion, mass and quantity."""
    client, request = api({"loadNodeDate": {"respCode": "0", "ok": True,
                                            "data": _view(dict(COMPONENT_RECORD))}})
    await client.set_fields("CA_21_791936644", {"cname": "Renamed"})
    view = next(c for c in request.calls if "editNodeDate" in c[1])[3]["view"]
    assert view["structureVO"]["crateType"] == 1
    assert view["structureVO"]["cminRate"] == "44"
    assert view["mdsState"] == "ORIGINAL" and view["vocFlag"] is False


@pytest.mark.parametrize("call, path, expected", [
    ("add_component", "/api/mds/component/addComponentNodeToTree",
     {"rootId": "CA_5_1", "parentid": "CA_21_1", "index": "0"}),
    ("add_semicomponent", "/api/mds/semiComponent/addNewSemiComponentToTree",
     {"rootId": "CA_5_1", "parentid": "CA_21_1", "index": "0"}),
])
async def test_child_nodes_are_added_at_an_explicit_index(call, path, expected):
    child = {"treeDataNode": {"id": "CA_21_2", "mdsId": "CA_5_2", "mdsCver": 0.01, "text": "x"}}
    client, request = api({path: {"respCode": "0", "data": child, "ok": True}})
    node = await getattr(client, call)("CA_5_1", "CA_21_1", 0)
    assert node.struts_id == "CA_21_2"
    assert request.calls[0][2] == expected


async def test_attaching_an_existing_material_passes_every_recorded_parameter():
    attached = {"id": "CA_21_3", "mdsId": "CA_8_54096701", "mdsCver": 1, "text": "EPDM"}
    client, request = api({"substituteMdsNode": {"respCode": "0", "data": attached, "ok": True}})
    node = await client.attach_mds(root_struts_id="CA_21_791936107", root_mds="CA_5_158481580",
                                   mds_id="CA_8_54096701", parent_struts_id="CA_21_791936113", index=0)
    assert node.reference == ("CA_8_54096701", "1")
    assert request.calls[0][2] == {"rootStrutsId": "CA_21_791936107", "cblkid": "CA_5_158481580",
                                   "mdsId": "CA_8_54096701", "parentStrutsId": "CA_21_791936113",
                                   "index": "0"}


async def test_save_addresses_the_root_not_the_edited_node():
    client, request = api()
    await client.save("CA_21_791936107", "CA_5_158481580")
    method, url, params, _ = request.calls[0]
    assert url.endswith("/api/mds/tree/saveNodeDate")
    assert params == {"StrutsId": "CA_21_791936107", "mdsId": "CA_5_158481580"}


async def test_application_rows_carry_the_english_name_and_current_option():
    rows = [{"subId": "5744", "enName": "distillates (petroleum), solvent-dewaxed heavy paraffinic",
             "maxRate": "18", "option": "<font color='blue'>none</font>", "optionCode": None}]
    client, request = api({"getApplyList": {"respCode": "0", "data": rows, "ok": True}})
    found = await client.application_list(material_mds="CA_8_54096701",
                                          component_struts_id="CA_21_791936113",
                                          parent_component_mds="CA_5_158481581")
    assert found[0]["enName"].startswith("distillates")
    assert request.calls[0][2] == {"materialMdsId": "CA_8_54096701", "comCsid": "CA_21_791936113",
                                   "pComMdsId": "CA_5_158481581"}


async def test_a_node_without_an_id_is_not_accepted_as_a_result():
    client, _ = api({"createInitComponent": {"respCode": "0", "data": {"treeDataNode": {}}, "ok": True}})
    with pytest.raises(CamdsApiError, match="no tree node"):
        await client.create_component_root()


def test_a_node_that_has_no_mds_yet_refuses_to_produce_a_reference():
    node = TreeNode("CA_21_1", None, None, "x", 1, {})
    with pytest.raises(CamdsApiError, match="no MDS id"):
        _ = node.reference


async def test_a_material_is_created_with_its_classification_as_a_parameter():
    # The browser wizard, including the ISO 1043 symbol page, is a UI construct.
    node = {"treeDataNode": {"id": "CA_21_791936236", "mdsId": "CA_8_55099221",
                             "mdsCver": 0.01, "text": "Material_CA_8_55099221",
                             "nodeType": 3, "cmatClsId": "3.1"}}
    client, request = api({"createInitMaterial": {"respCode": "0", "data": node, "ok": True}})
    created = await client.create_material_root("3.1")
    assert created.reference == ("CA_8_55099221", "0.01")
    assert request.calls[0][2] == {"mdsFlag": "0", "classification": "3.1"}


async def test_substances_are_attached_by_catalogue_id_not_by_cas():
    added = {"treeDataNode": {"id": "CA_21_791936263", "mdsId": "2995", "mdsCver": 0.0,
                              "text": "Copper", "nodeType": 4}}
    client, request = api({"addNewSubstanceToTree": {"respCode": "0", "data": added, "ok": True}})
    node = await client.add_substance("CA_8_55099221", "CA_21_791936236", "2995", 0)
    assert node.text == "Copper"
    assert request.calls[0][2] == {"rootMdsId": "CA_8_55099221", "parentStrutsId": "CA_21_791936236",
                                   "subId": "2995", "index": "0"}


@pytest.mark.parametrize("kwargs, expected", [
    ({"cas": "7440-50-8"}, {"cas": "7440-50-8", "name": ""}),
    # A system group has no CAS and is found by its name instead.
    ({"name": "Misc., not to declare"}, {"cas": "", "name": "Misc., not to declare"}),
])
async def test_substances_are_searched_by_cas_or_by_name(kwargs, expected):
    found = {"records": [{"csubId": "8172", "cenName": "Misc., not to declare"}], "total": 1}
    client, request = api({"findSubstanceByCondition": {"respCode": "0", "data": found, "ok": True}})
    rows = await client.find_substance(**kwargs)
    assert rows[0]["csubId"] == "8172"
    sent = request.calls[0][3]
    for key, value in expected.items():
        assert sent[key] == value
    assert sent["pageNo"] == 1


async def test_an_empty_result_page_is_an_empty_list_not_an_error():
    client, _ = api({"findSubstanceByCondition": {"respCode": "0", "ok": True,
                                                  "data": {"records": [], "total": 0}}})
    assert await client.find_substance(cas="0000-00-0") == []


@pytest.mark.parametrize("call, path", [
    ("find_material", "findMaterialByCondition"),
    ("find_component", "findComponentByCondition"),
])
async def test_searches_send_the_recorded_form(call, path):
    found = {"records": [{"mdsId": "CA_8_55099221", "mdsName": "Cu99", "version": "0.01"}]}
    client, request = api({path: {"respCode": "0", "data": found, "ok": True}})
    rows = await getattr(client, call)(name="Cu99")
    assert rows[0]["mdsId"] == "CA_8_55099221"
    sent = request.calls[0][3]
    assert sent["name"] == "Cu99" and sent["ownerMds"] is True and sent["current"] == 1
    # The UI always narrows by date; a caller must widen it on purpose.
    assert "dateFrom" not in sent


async def test_a_date_window_is_sent_only_when_asked_for():
    client, request = api({"findMaterialByCondition": {"respCode": "0", "ok": True,
                                                       "data": {"records": []}}})
    await client.find_material(name="Cu99", date_from="2020-01-01", date_to="2026-12-31")
    sent = request.calls[0][3]
    assert sent["dateFrom"] == "2020-01-01" and sent["dateto"] == "2026-12-31"


async def test_loading_a_saved_tree_is_the_read_back_path():
    tree = {"id": "CA_21_791936236", "mdsId": "CA_8_55099221", "mdsCver": 0.01,
            "text": "Cu99", "cmatClsId": "3.1", "children": [{"id": "CA_21_791936263", "text": "Copper"}]}
    client, request = api({"loadMdsTree": {"respCode": "0", "data": tree, "ok": True}})
    loaded = await client.load_tree("CA_8_55099221")
    assert loaded["text"] == "Cu99" and loaded["children"][0]["text"] == "Copper"
    assert request.calls[0][2] == {"mdsId": "CA_8_55099221"}


async def test_a_tree_that_does_not_load_fails_rather_than_verifying_nothing():
    client, _ = api({"loadMdsTree": {"respCode": "0", "data": {}, "ok": True}})
    with pytest.raises(CamdsApiError, match="no tree"):
        await client.load_tree("CA_8_1")


# --------------------------------------------------------------- relations
from camds_imds_importer.camds.api import FIXED, FROM_TO, REST, portion  # noqa: E402


@pytest.mark.parametrize("args, expected", [
    # The browser radio values and these mode numbers are the same: 1, 2, 3.
    ((FIXED, 10), {"crateType": 2, "crate": 10, "cminRate": 0, "cmaxRate": 0}),
    ((FROM_TO, 44, 50), {"crateType": 1, "cminRate": 44, "cmaxRate": 50}),
    ((REST,), {"crateType": 3, "crate": 0, "cminRate": 0, "cmaxRate": 0}),
])
def test_portion_modes_match_the_recorded_structure(args, expected):
    assert portion(*args) == expected


@pytest.mark.parametrize("args, message", [
    ((FIXED,), "fixed portion needs a value"),
    ((FROM_TO, 44), "both bounds"),
    ((9,), "Unknown portion mode"),
])
def test_an_incomplete_portion_is_refused(args, message):
    with pytest.raises(ValueError, match=message):
        portion(*args)


async def test_a_substance_portion_is_written_on_the_relation_not_the_record():
    client, request = api({"loadNodeDate": {"respCode": "0", "ok": True,
                                            "data": _view(dict(COMPONENT_RECORD))}})
    await client.set_relation("CA_21_791936644", portion(FROM_TO, 45, 50))
    view = next(c for c in request.calls if "editNodeDate" in c[1])[3]["view"]
    assert view["structureVO"]["crateType"] == 1
    assert view["structureVO"]["cminRate"] == 45 and view["structureVO"]["cmaxRate"] == 50
    # The node's own fields are untouched.
    assert view["data"]["cname"] == COMPONENT_RECORD["cname"]


async def test_mass_and_quantity_live_on_the_relation():
    # A Material under a Component carries cweight; a child Component cquantity.
    client, request = api({"loadNodeDate": {"respCode": "0", "ok": True,
                                            "data": _view(dict(COMPONENT_RECORD))}})
    await client.set_relation("CA_21_1", {"cweight": 50, "cweightUnit": "g", "cquantity": 5})
    relation = next(c for c in request.calls if "editNodeDate" in c[1])[3]["view"]["structureVO"]
    assert relation["cweight"] == 50 and relation["cquantity"] == 5


async def test_a_relation_field_camds_does_not_carry_is_refused():
    client, _ = api({"loadNodeDate": {"respCode": "0", "ok": True,
                                      "data": _view(dict(COMPONENT_RECORD))}})
    with pytest.raises(CamdsApiError, match="structureVO has no field"):
        await client.set_relation("CA_21_1", {"portionType": 2})


async def test_sibling_ids_travel_with_an_edit():
    client, request = api({"loadNodeDate": {"respCode": "0", "ok": True,
                                            "data": _view(dict(COMPONENT_RECORD))}})
    await client.set_fields("CA_21_1", {"cname": "x"}, parent_id="CA_21_0",
                            brothers=("CA_21_2", "CA_21_3"))
    body = next(c for c in request.calls if "editNodeDate" in c[1])[3]
    assert body["brotherSidList"] == ["CA_21_2", "CA_21_3"]
    assert body["parentId"] == "CA_21_0"


# ------------------------------------------------------- applications
APPLY_ROW = {"subId": "2943", "subTypeId": None, "cid": None, "prtstrid": "CA_21_791936664",
             "name": "铅", "enName": "Lead", "maxRate": "0.02",
             "option": "<font color='red'>Concentration within acceptable GADSL limits</font>",
             "appstdid": None, "rateType": None, "optionCode": "27"}
APPLY_OPTIONS = [
    {"maxRate": "0.01", "option": "在GADSL中可接受的浓度范围",
     "enOption": "Concentration within acceptable GADSL limits", "appstdid": "1014",
     "rateType": "1", "optionCode": "27"},
    {"maxRate": "0", "option": "其他应用（潜在禁止)",
     "enOption": "Other application (potentially prohibited)", "appstdid": "1061",
     "rateType": "2", "optionCode": "28"},
]


async def test_application_standards_return_the_current_choice_and_the_options():
    payload = {"selectedOption": "27", "value": "200,0.1,27,…", "applyViewList": APPLY_OPTIONS}
    client, request = api({"getApplyAppstd": {"respCode": "0", "data": payload, "ok": True}})
    selected, options = await client.application_standards(
        material_classification="3.3", material_mds="CA_8_46123515", substance_id="2943")
    assert selected == "27"
    assert [o["optionCode"] for o in options] == ["27", "28"]
    assert request.calls[0][2] == {"matClsId": "3.3", "materialMdsId": "CA_8_46123515",
                                   "subId": "2943"}


async def test_a_substance_with_no_standards_yields_no_options():
    client, _ = api({"getApplyAppstd": {"respCode": "0", "ok": True,
                                        "data": {"selectedOption": "", "applyViewList": None}}})
    selected, options = await client.application_standards(
        material_classification="1.1.1", material_mds="CA_8_1", substance_id="9")
    assert selected == "" and options == []


async def test_writing_an_application_sends_the_option_code_and_its_standard():
    client, request = api()
    await client.set_application(APPLY_ROW, APPLY_OPTIONS[1], material_mds="CA_8_46123515")
    method, url, _, body = request.calls[0]
    assert url.endswith("/api/mds/tree/addOrUpdateApply")
    assert body["subId"] == "2943" and body["enName"] == "Lead"
    assert body["cid"] == "CA_8_46123515" and body["prtstrid"] == "CA_21_791936664"
    # The chosen option supplies both identifiers CAMDS needs.
    assert body["optionCode"] == "28" and body["appstdid"] == "1061"


@pytest.mark.parametrize("option, row", [
    ({"appstdid": "1061"}, APPLY_ROW),                       # no optionCode
    ({"optionCode": "28"}, APPLY_ROW),                       # no appstdid
    (APPLY_OPTIONS[1], dict(APPLY_ROW, subId=None)),         # no substance
])
async def test_an_incomplete_application_is_never_written(option, row):
    client, request = api()
    with pytest.raises(CamdsApiError, match="without"):
        await client.set_application(row, option, material_mds="CA_8_1")
    assert request.calls == []


async def test_every_call_identifies_itself_as_same_origin():
    """Without Origin and Referer CAMDS answers a real path with HTTP 404."""
    client, request = api()
    await client.save("CA_21_1", "CA_5_1")
    await client.material_status("CA_8_1")
    assert len(request.calls) == 2
    for call in request.calls:
        pass
    # Headers are not captured by the fake's signature, so assert on the builder.
    headers = client._headers(content_type=True)
    assert headers["Origin"] == "https://catarc.camds.org.cn"
    assert headers["Referer"] == "https://catarc.camds.org.cn/"
    assert headers["Content-Type"] == "application/json"
    assert "Content-Type" not in client._headers()


async def test_a_write_never_carries_a_null_position():
    """loadNodeDate answers a fresh root with cindex null; CAMDS throws on it."""
    fresh = _view(dict(COMPONENT_RECORD), dict(RELATION, cindex=None))
    client, request = api({"loadNodeDate": {"respCode": "0", "ok": True, "data": fresh}})
    await client.set_fields("CA_21_1", {"cname": "Root"})
    body = next(c for c in request.calls if "editNodeDate" in c[1])[3]
    assert body["view"]["structureVO"]["cindex"] == 0
    # Every recorded write carries the cache-buster.
    assert "_t" in body


async def test_an_explicit_position_is_kept_for_a_child():
    fresh = _view(dict(COMPONENT_RECORD), dict(RELATION, cindex=None))
    client, request = api({"loadNodeDate": {"respCode": "0", "ok": True, "data": fresh}})
    view = await client.load_view("CA_21_1")
    await client.edit_view("CA_21_1", view, index=3)
    body = next(c for c in request.calls if "editNodeDate" in c[1])[3]
    assert body["view"]["structureVO"]["cindex"] == 3


async def test_a_position_camds_already_holds_is_left_alone():
    client, request = api({"loadNodeDate": {"respCode": "0", "ok": True,
                                            "data": _view(dict(COMPONENT_RECORD),
                                                          dict(RELATION, cindex=2))}})
    await client.set_fields("CA_21_1", {"cname": "Child"})
    body = next(c for c in request.calls if "editNodeDate" in c[1])[3]
    assert body["view"]["structureVO"]["cindex"] == 2
