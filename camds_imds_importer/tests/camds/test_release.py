"""Publishing a Material, from `release_material.har`.

Release is outward-facing and cannot be undone from here, so it is off unless
the operator asks, only ever applies to a Material this run created, and
happens only once CAMDS itself says the MDS is clean.
"""
import pytest

from camds_imds_importer.camds.api import RECYCLATE_NONE, CamdsApi, CamdsApiError
from camds_imds_importer.camds.api_backend import ApiBackend

USER, ORG, CONTACT = "CA_2_111138", "CA_3_3386", "8b18501f7b8c9d3e8a11fcdfdc82d385"


class Camds:
    def __init__(self, errors=(1, 0), contacts=None, creator=None):
        # The recorded session validates twice. The first is the form opening -
        # it reported one error, before the recyclate question was answered -
        # and only the second decides whether to publish.
        self.calls, self.bodies = [], {}
        self.errors = list(errors)
        self.contacts = contacts if contacts is not None else [
            {"userId": "CA_2_102261", "name": "Liu Yan", "scid": "other"},
            {"userId": USER, "name": "Le Van Hanh", "scid": CONTACT},
        ]
        self.creator = creator if creator is not None else {
            "userId": USER, "enterprsieId": ORG, "name": "Le Van Hanh"}

    async def post(self, url, params=None, data=None, headers=None, timeout=None):
        name = url.rsplit("/", 1)[-1]
        self.calls.append(name)
        self.bodies[name] = {"params": params or {}, "data": data or {}}
        if name == "mdsValidate":
            payload = {"errorSize": self.errors.pop(0) if self.errors else 0}
        elif name == "getMdsCreator":
            payload = self.creator
        elif name == "findMDSContacterViewList":
            payload = self.contacts
        elif name == "loadMdsTree":
            # CAMDS bumps the version when an MDS is released.
            version = 1.0 if "innerPublish" in self.calls else 0.01
            payload = {"id": "CA_21_1", "mdsId": "CA_8_9", "mdsCver": version, "text": "Cu99"}
        elif name == "loadNodeDate":
            payload = {"data": {"cname": "Cu99"}, "structureVO": {"cindex": 0, "cnodeType": 3,
                                                                  "recycledmaterials": 2},
                       "materialRecyclateVO": None, "mdsState": None, "refed": False,
                       "state": "ORIGINAL", "structState": "ORIGINAL", "vocFlag": False,
                       "treeDataNode": {"id": "CA_21_1", "mdsId": "CA_8_9", "mdsCver": 0.01}}
        else:
            payload = None

        class Response:
            status = 200

            @staticmethod
            async def json():
                return {"respCode": "0", "ok": True, "data": payload}
        return Response

    async def get(self, url, params=None, headers=None, timeout=None):
        return await self.post(url, params, None, headers, timeout)


def backend(camds):
    return ApiBackend(CamdsApi(camds, base_url="https://camds.test"))


async def test_the_release_follows_the_recorded_order():
    camds = Camds()
    await backend(camds).release_material(("CA_8_9", "0.01"))
    order = [c for c in camds.calls if c in {
        "canbeModifyMx", "bStandardMaterials", "editMaterialRecyclateVO", "saveNodeDate",
        "getMdsCreator", "findMDSContacterViewList", "saveSupplierDataView",
        "mdsValidate", "innerPublish"}]
    # The form's own order. Going straight to the recyclate write was refused
    # with a generic program exception even carrying the whole record.
    assert order == ["canbeModifyMx", "bStandardMaterials", "mdsValidate",
                     "bStandardMaterials", "editMaterialRecyclateVO", "saveNodeDate",
                     "getMdsCreator", "findMDSContacterViewList", "saveSupplierDataView",
                     "mdsValidate", "innerPublish"]


async def test_the_recyclate_question_is_answered_no():
    camds = Camds()
    await backend(camds).release_material(("CA_8_9", "0.01"))
    sent = camds.bodies["editMaterialRecyclateVO"]["data"]["materialRecyclateVO"]
    assert sent["containRecyclate"] == 2, "2 is No"
    assert sent == RECYCLATE_NONE


async def test_the_operator_is_whoever_is_signed_in():
    """Nothing about the user is configured, so a build handed to someone else
    releases as them rather than as whoever recorded this."""
    camds = Camds(creator={"userId": "CA_2_999", "enterprsieId": "CA_3_777"},
                  contacts=[{"userId": "CA_2_999", "scid": "theirs"}])
    await backend(camds).release_material(("CA_8_9", "0.01"))
    sent = camds.bodies["saveSupplierDataView"]["params"]
    assert sent["userId"] == "CA_2_999"
    assert sent["orgId"] == "CA_3_777"
    assert sent["supplierContactId"] == "theirs"
    assert camds.bodies["findMDSContacterViewList"]["params"]["orgId"] == "CA_3_777"


async def test_the_contact_chosen_is_the_signed_in_user_not_the_first_row():
    camds = Camds()
    await backend(camds).release_material(("CA_8_9", "0.01"))
    assert camds.bodies["saveSupplierDataView"]["params"]["supplierContactId"] == CONTACT


async def test_a_material_camds_reports_errors_on_is_not_published():
    """The second validate is the gate. The first is the form opening, and the
    recording shows it reporting an error that answering the question clears -
    treating that one as the gate would refuse every release."""
    camds = Camds(errors=(1, 3))
    with pytest.raises(CamdsApiError, match="3 validation error"):
        await backend(camds).release_material(("CA_8_9", "0.01"))
    assert "innerPublish" not in camds.calls


async def test_the_opening_validation_is_not_the_gate():
    """It reported one error in the recording, before the recyclate question
    was answered, and the release went ahead once it was."""
    camds = Camds(errors=(1, 0))
    await backend(camds).release_material(("CA_8_9", "0.01"))
    assert "innerPublish" in camds.calls


async def test_an_ambiguous_contact_stops_rather_than_guessing():
    camds = Camds(contacts=[{"userId": USER, "scid": "a"}, {"userId": USER, "scid": "b"}])
    with pytest.raises(CamdsApiError, match="not one"):
        await backend(camds).release_material(("CA_8_9", "0.01"))
    assert "innerPublish" not in camds.calls


async def test_an_unknown_signed_in_user_stops_rather_than_guessing():
    camds = Camds(creator={})
    with pytest.raises(CamdsApiError, match="who is signed in"):
        await backend(camds).release_material(("CA_8_9", "0.01"))
    assert "innerPublish" not in camds.calls


async def test_release_is_off_unless_asked_for(tmp_path):
    """Publishing cannot be undone from here, so it is never the default."""
    import inspect

    from camds_imds_importer.camds.tree_import import TreeImporter

    assert inspect.signature(TreeImporter.run).parameters["release"].default is False


async def test_only_a_material_this_run_created_is_released(tmp_path):
    """A reused or mapped Material is somebody else's to publish."""
    import inspect

    from camds_imds_importer.camds.tree_import import TreeImporter

    body = inspect.getsource(TreeImporter.run)
    released_at = body.index("await self.io.release_material(ref)")
    verified_at = body.index('record("material_readback_verified"')
    assert verified_at < released_at, "released only after this run verified it"
    reused_at = body.index('record("existing_material_reused"')
    assert reused_at < verified_at, "a reused Material takes the continue above"


async def test_releasing_returns_the_version_camds_gave_it():
    """Releasing bumps 0.01 to 1. Keeping the draft's version attached one MDS
    and then verified against another - "expected CA_8_.../0.01, CAMDS has
    CA_8_.../1" ended a run in which all 52 Materials had published fine."""
    camds = Camds()
    assert await backend(camds).release_material(("CA_8_9", "0.01")) == ("CA_8_9", "1")


async def test_the_new_version_is_what_the_rest_of_the_run_uses(tmp_path):
    from camds_imds_importer.camds.import_plan import ImportRequest
    from camds_imds_importer.camds.tree_import import TreeImporter

    attached = []

    class Backend:
        reporter = None

        async def prepare(self): pass
        async def read_back_findings(self): return []
        async def can_reenter_saved(self): return False
        async def saved_children(self, path, at=(0, 1)): return []
        async def find_existing_material(self, node): return None
        async def find_existing_component(self, node, resolved): return None
        async def create_root(self, node, on_allocated=None, existing=None):
            self.ref = ("CA_8_9", "0.01") if node["node_type"] == "MATERIAL" else ("CA_5_1", "0.01")
            if on_allocated:
                on_allocated(self.ref)
            return self.ref
        async def save(self): pass
        async def add_substance(self, name, node): return node["name"]
        async def release_material(self, ref): return ("CA_8_9", "1")
        async def open_saved(self, kind, ref): self.ref = tuple(ref)
        async def value(self, label): return "Cu99"
        async def identity(self): return self.ref
        async def verify_value(self, label, expected): pass
        async def verify_child_count(self, path, count, at=(0, 1)): pass
        async def verify_substance(self, path, node, at=(0, 1)): pass
        async def select(self, path, at=(0, 1)):
            # Read-back selects the attached Material and asks which MDS it is.
            if path[-1] == "Cu99" and attached:
                self.ref = attached[-1]
        async def add_material(self, path, node, ref, at=(0, 1), by_portion=False, reuse_index=None):
            attached.append(tuple(ref))
            self.ref = tuple(ref)
            return "Cu99"

    root = {"uid": "r", "node_type": "COMPONENT", "name": "Part", "weight_g": 1.0,
            "children": [{"uid": "m", "node_type": "MATERIAL", "name": "Cu99",
                          "classification": "1.1.1", "weight_g": 1.0,
                          "children": [{"uid": "s", "node_type": "SUBSTANCE", "name": "Copper",
                                        "cas_number": "7440-50-8", "percentage": 100,
                                        "children": []}]}]}
    await TreeImporter(Backend(), tmp_path).run(ImportRequest(root), release=True)
    assert attached == [("CA_8_9", "1")], "the released version, not the draft"
