"""CAMDS JSON API client, derived from a recorded create-component session.

Every endpoint, parameter and field name here comes from
`camds/CREATE_COMPONENT_API.md`, which documents the HAR capture. Nothing is
invented: an operation that the capture does not show is absent from this
module rather than guessed.

Authentication stays in the browser. `POST /api/login` takes an
RSA/AES-encrypted username and password plus a `verifyCode` from the CAPTCHA,
so the session is established once in the headed window and these calls reuse
its cookies through Playwright's request context.

The write pattern CAMDS itself uses is load, mutate, post back:

    node = await api.load_node(sid)      # full record
    node["cname"] = "New name"
    await api.edit_node(sid, node)       # whole record, not a patch
    await api.save(root_sid, root_mds)   # persists the tree
"""
from __future__ import annotations

import inspect
import re
import time
from dataclasses import dataclass
from typing import Any

BASE_URL = "https://catarc.camds.org.cn"

# Node kinds as CAMDS labels its tables; the table name identifies the record.
COMPONENT_TABLE = "t_component_node"
SEMICOMPONENT_TABLE = "t_semi_component_node"
MATERIAL_TABLE = "t_material"

# Editable fields observed in the capture, by table.
NAME = "cname"
NUMBER = "csymbol"
MASS_PER_ITEM = "cmeaWeightPerItem"   # Component: Measured Mass per Item
SEMI_MASS = "cweight"                 # Semicomponent: Mass
WEIGHT_UNIT = "cweightUnit"           # observed value "g"
CLASSIFICATION = "cmatClsId"          # Material classification code, e.g. "5.3"

# One editNodeDate carries the node record and its parent relation together.
VIEW_KEYS = ("data", "materialRecyclateVO", "mdsState", "refed",
             "state", "structState", "structureVO", "vocFlag")

# structureVO: how a node sits inside its parent.
REL_QUANTITY = "cquantity"      # Component count within the parent
REL_MASS = "cweight"            # Material mass within a Component
REL_MASS_UNIT = "cweightUnit"   # observed "g"
REL_MODE = "crateType"          # portion mode, below
REL_RATE = "crate"              # the Fixed value, or the value a range resolves to
REL_INDEX = "cindex"            # position among siblings; never null in a write
REL_RECYCLATE = "recycledmaterials"

# A catalogue search row and an attached Substance node record name the same
# things differently. Mixing them up silently reads every field as missing.
SEARCH_ID, SEARCH_CAS, SEARCH_NAME = "csid", "cas", "enName"
NODE_ID, NODE_CAS, NODE_NAME = "csubId", "ccasCode", "cenName"

# A node that points at another MDS is returned under a prefixed tree id -
# "ref1_-CA_21_612736365" - but every call addresses it by the bare id. Posting
# the prefixed one is answered with a generic "程序异常".
REFERENCED_ID = re.compile(r"^.*?(CA_21_\d+)$")


def addressable(tree_id: str) -> str:
    """The id `loadNodeDate` accepts, from the id `loadMdsTree` returns."""
    found = REFERENCED_ID.match(tree_id or "")
    return found.group(1) if found else tree_id


# nodeType as CAMDS numbers it in structureVO.
COMPONENT_NODE, SEMICOMPONENT_NODE, MATERIAL_NODE, SUBSTANCE_NODE = 1, 2, 3, 4
# The value a newly created Material was recorded with.
NEW_MATERIAL_RECYCLATE = 2
REL_MIN = "cminRate"
REL_MAX = "cmaxRate"

# Portion modes, the same numbering the browser's radio inputs use.
FROM_TO, FIXED, REST = 1, 2, 3


def portion(mode: int, low: float | None = None, high: float | None = None) -> dict:
    """The structureVO fields for one portion, by mode.

    A Substance inside a Material and a Material inside a Semicomponent are both
    declared this way; a Material inside a Component carries a mass instead.
    """
    if mode == FIXED:
        if low is None:
            raise ValueError("A fixed portion needs a value")
        return {REL_MODE: FIXED, REL_RATE: low, REL_MIN: 0, REL_MAX: 0}
    if mode == FROM_TO:
        if low is None or high is None:
            raise ValueError("A From-To portion needs both bounds")
        return {REL_MODE: FROM_TO, REL_MIN: low, REL_MAX: high}
    if mode == REST:
        return {REL_MODE: REST, REL_RATE: 0, REL_MIN: 0, REL_MAX: 0}
    raise ValueError(f"Unknown portion mode {mode!r}")


def _apply(view: dict, section: str, changes: dict, struts_id: str) -> None:
    record = view.get(section)
    if not isinstance(record, dict):
        raise CamdsApiError(f"{struts_id}: CAMDS returned no {section} to change")
    unknown = [key for key in changes if key not in record]
    if unknown:
        # A field CAMDS does not carry would be silently dropped.
        raise CamdsApiError(f"{struts_id}: {section} has no field(s) {', '.join(sorted(unknown))}")
    record.update(changes)


class CamdsApiError(RuntimeError):
    """CAMDS answered, but refused the operation."""


@dataclass(frozen=True, slots=True)
class TreeNode:
    """One node of the MDS tree as the API returns it."""

    struts_id: str          # tree identity, e.g. CA_21_791936107
    mds_id: str | None      # MDS identity, e.g. CA_5_158481580
    version: float | None
    text: str
    node_type: int | None
    raw: dict

    @classmethod
    def from_payload(cls, payload: dict) -> "TreeNode":
        node = payload.get("treeDataNode") or payload
        if not node.get("id"):
            raise CamdsApiError(f"CAMDS returned no tree node: {str(payload)[:200]}")
        return cls(node["id"], node.get("mdsId"), node.get("mdsCver"),
                   node.get("text") or "", node.get("nodeType"), node)

    @property
    def reference(self) -> tuple[str, str]:
        """The ID / version pair the operator sees, as CAMDS formats it."""
        if not self.mds_id or self.version is None:
            raise CamdsApiError(f"Node {self.struts_id} has no MDS id and version yet")
        return self.mds_id, format(self.version, ".12g")


async def _peek(response, limit: int = 200) -> str:
    """The first bytes CAMDS sent back, for a message a human can act on."""
    try:
        text = response.text()
        if inspect.isawaitable(text):
            text = await text
        return " ".join(str(text).split())[:limit] or "<empty body>"
    except Exception:
        return "<body unreadable>"


def _stamp() -> int:
    """The `_t` cache-buster every call carries, in whole seconds."""
    return int(time.time())


def _records(payload: Any) -> list:
    """Search results arrive paged, under `records`."""
    if isinstance(payload, dict):
        return payload.get("records") or []
    return payload or []


def search_form(*, name: str = "", symbol: str = "", own: bool = True,
                published: bool = False, approved: bool = False,
                date_from: str | None = None, date_to: str | None = None,
                page: int = 1, size: int = 10) -> dict:
    """The search payload as the CAMDS pages send it.

    The UI always fills a date window, and its default covers only the last
    month, so a caller looking for an older MDS has to widen it deliberately.
    """
    form = {
        "suppliesList": [], "allSupplies": False, "modifiedRecent": False,
        "ownerMds": own, "ownerUnit": own, "latestVer": False,
        "tradeNameLanguage": "", "datePublished": False, "owner": False,
        "mdsApproved": approved, "mdsPublished": published,
        "size": size, "current": page, "overdueMds": "", "amassFlag": 0,
        "name": name, "symbol": symbol, "_t": _stamp(),
    }
    if date_from:
        form["dateFrom"] = date_from
    if date_to:
        form["dateto"] = date_to
    return form


class CamdsApi:
    def __init__(self, request, base_url: str = BASE_URL) -> None:
        # `request` is a Playwright APIRequestContext from the signed-in browser
        # context, so cookies and the CAPTCHA-gated session come with it.
        self.request = request
        self.base_url = base_url.rstrip("/")

    def _headers(self, content_type: bool = False) -> dict:
        # CAMDS is same-origin XHR; the pages always send Origin and Referer.
        headers = {"Accept": "application/json, text/plain, */*",
                   "Origin": self.base_url, "Referer": self.base_url + "/"}
        if content_type:
            headers["Content-Type"] = "application/json"
        return headers

    async def _post(self, path: str, params: dict | None = None, payload: dict | None = None) -> Any:
        body = {"_t": _stamp()} if payload is None else payload
        url = self.base_url + path
        response = await self.request.post(
            url, params={k: str(v) for k, v in (params or {}).items()},
            data=body, headers=self._headers(content_type=True))
        return await self._unwrap(url, response)

    async def _get(self, path: str, params: dict | None = None) -> Any:
        merged = {"_t": _stamp(), **{k: str(v) for k, v in (params or {}).items()}}
        url = self.base_url + path
        response = await self.request.get(url, params=merged, headers=self._headers())
        return await self._unwrap(url, response)

    @staticmethod
    async def _unwrap(path: str, response) -> Any:
        if response.status != 200:
            # Name the URL and quote CAMDS: a 404 on a recorded path means the
            # request did not arrive as the browser sends it.
            raise CamdsApiError(f"{path} returned HTTP {response.status}: "
                                f"{await _peek(response)}")
        try:
            # Playwright decodes the body asynchronously.
            body = response.json()
            if inspect.isawaitable(body):
                body = await body
        except CamdsApiError:
            raise
        except Exception as exc:
            # An expired session answers with the login page, not with JSON.
            raise CamdsApiError(
                f"{path} did not return JSON; the CAMDS session may have expired. "
                f"CAMDS said: {await _peek(response)}") from exc
        # A refusal still arrives as HTTP 200, so respCode is what decides.
        if str(body.get("respCode", "0")) != "0" or body.get("ok") is False:
            raise CamdsApiError(f"{path} refused: {body.get('message') or body}")
        return body.get("data")

    # ----------------------------------------------------------- create nodes
    async def create_component_root(self) -> TreeNode:
        """Allocate a new Component MDS. This is what consumes a CAMDS ID."""
        return TreeNode.from_payload(
            await self._post("/api/mds/component/createInitComponent", {"mdsFlag": 0}))

    async def add_component(self, root_mds: str, parent_struts_id: str, index: int) -> TreeNode:
        return TreeNode.from_payload(await self._post(
            "/api/mds/component/addComponentNodeToTree",
            {"rootId": root_mds, "parentid": parent_struts_id, "index": index}))

    async def add_semicomponent(self, root_mds: str, parent_struts_id: str, index: int) -> TreeNode:
        return TreeNode.from_payload(await self._post(
            "/api/mds/semiComponent/addNewSemiComponentToTree",
            {"rootId": root_mds, "parentid": parent_struts_id, "index": index}))

    async def attach_mds(self, *, root_struts_id: str, root_mds: str, mds_id: str,
                         parent_struts_id: str, index: int) -> TreeNode:
        """Reference an existing MDS (a Material) under a parent node."""
        return TreeNode.from_payload(await self._post(
            "/api/mds/tree/substituteMdsNode",
            {"rootStrutsId": root_struts_id, "cblkid": root_mds, "mdsId": mds_id,
             "parentStrutsId": parent_struts_id, "index": index}))

    # ------------------------------------------------------------ read/write
    async def load_view(self, struts_id: str) -> dict:
        """Everything CAMDS holds about one node, in the shape edit expects back.

        `loadNodeDate` answers with the node record under `data` *and* the
        parent relation under `structureVO`. Both travel back together: posting
        only the record would drop the relation, and with it the portion, mass
        and quantity.
        """
        payload = await self._post("/api/mds/tree/loadNodeDate", {"strutsId": struts_id})
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise CamdsApiError(f"loadNodeDate returned no record for {struts_id}")
        # Everything is returned, including `treeDataNode` with the MDS id and
        # version; `edit_view` narrows it to the keys CAMDS accepts back.
        return dict(payload)

    async def load_node(self, struts_id: str) -> dict:
        """Just the node's own fields."""
        return (await self.load_view(struts_id))["data"]

    async def edit_view(self, struts_id: str, view: dict, *, parent_id: str | None = None,
                        brothers: tuple[str, ...] = (), index: int | None = None) -> Any:
        """Post a whole view back. CAMDS replaces the node, it does not patch."""
        sending = {key: view.get(key) for key in VIEW_KEYS}
        relation = sending.get("structureVO")
        if isinstance(relation, dict):
            sending["structureVO"] = relation = dict(relation)
            if relation.get(REL_INDEX) is None:
                # loadNodeDate answers a freshly created root with a null cindex,
                # but no recorded write ever carries one.
                relation[REL_INDEX] = 0 if index is None else index
            if relation.get(REL_RECYCLATE) is None and relation.get("cnodeType") == MATERIAL_NODE:
                # Every recorded Material write carries this; Components and
                # Substances leave it null. A new Material was recorded as 2, an
                # attached reference as 0 - and an attached one arrives already
                # set, so only a new Material reaches this line. CAMDS answers a
                # null here with a generic "程序异常".
                relation[REL_RECYCLATE] = NEW_MATERIAL_RECYCLATE
        return await self._post("/api/mds/tree/editNodeDate", payload={
            "brotherSidList": list(brothers),
            "editedStructId": struts_id,
            "parentId": parent_id,
            "view": sending,
            # Every recorded write carries the cache-buster.
            "_t": _stamp(),
        })

    async def set_fields(self, struts_id: str, changes: dict, *, relation: dict | None = None,
                         **edit_options) -> dict:
        """Load, apply node fields and relation fields, and post the view back."""
        view = await self.load_view(struts_id)
        _apply(view, "data", changes, struts_id)
        if relation:
            _apply(view, "structureVO", relation, struts_id)
        await self.edit_view(struts_id, view, **edit_options)
        return view

    async def set_relation(self, struts_id: str, changes: dict, **edit_options) -> dict:
        """Change how a node sits in its parent: portion, mass or quantity."""
        return await self.set_fields(struts_id, {}, relation=changes, **edit_options)

    async def save(self, root_struts_id: str, root_mds: str) -> None:
        await self._post("/api/mds/tree/saveNodeDate",
                         {"StrutsId": root_struts_id, "mdsId": root_mds})

    async def create_material_root(self, classification: str) -> TreeNode:
        """Allocate a new Material MDS in the given classification.

        The classification travels as a query parameter, so the browser's
        two-step wizard - including the ISO 1043 symbol page that polymer
        classifications open - is a UI construct the API does not impose.
        """
        return TreeNode.from_payload(await self._post(
            "/api/mds/material/createInitMaterial", {"mdsFlag": 0, "classification": classification}))

    async def add_substance(self, root_mds: str, parent_struts_id: str,
                            substance_id: str, index: int) -> TreeNode:
        """Attach a catalogue Substance. `substance_id` is `csubId`, not a CAS."""
        return TreeNode.from_payload(await self._post(
            "/api/mds/tree/addNewSubstanceToTree",
            {"rootMdsId": root_mds, "parentStrutsId": parent_struts_id,
             "subId": substance_id, "index": index}))

    # ---------------------------------------------------------------- lookups
    async def find_substance(self, *, cas: str = "", name: str = "",
                             page: int = 1, size: int = 10) -> list:
        """Find catalogue Substances by CAS or by name.

        A system group such as "Misc., not to declare" carries the CAS
        placeholder `system` and is found by name.
        """
        found = await self._post("/api/common/substance/findSubstanceByCondition", payload={
            # CAMDS does not trim: a stray space around a CAS finds nothing.
            "pageNo": page, "pageSize": size, "name": name.strip(), "cas": cas.strip(),
            "prohibit": "3", "declare": "3", "svhc": "2", "_t": _stamp(),
        })
        return _records(found)

    async def find_material(self, **criteria) -> list:
        return _records(await self._post("/api/mds/findMds/findMaterialByCondition",
                                         payload=search_form(**criteria)))

    async def find_component(self, **criteria) -> list:
        return _records(await self._post("/api/mds/findMds/findComponentByCondition",
                                         payload=search_form(**criteria)))

    async def load_tree(self, mds_id: str) -> dict:
        """The whole saved tree of one MDS: the read-back CAMDS itself uses."""
        tree = await self._post("/api/mds/tree/loadMdsTree", {"mdsId": mds_id})
        if not isinstance(tree, dict) or not tree.get("id"):
            raise CamdsApiError(f"loadMdsTree returned no tree for {mds_id}")
        return tree

    async def can_modify(self, mds_id: str) -> Any:
        """CAMDS asks this before it reads a referenced MDS node."""
        return await self._post("/api/mds/tree/canbeModifyMx", {"mdsId": mds_id})

    async def is_standard_material(self, mds_id: str) -> Any:
        """Asked after a referenced Material is read, before its substances are."""
        return await self._post("/api/mds/tree/isStandMaterial", {"mdsId": mds_id})

    async def material_status(self, mds_id: str) -> Any:
        return await self._get(f"/api/mds/tree/getMaterialStatus/{mds_id}")

    async def mds_status(self, mds_id: str) -> Any:
        return await self._get(f"/api/mds/tree/getMdsStatus/{mds_id}")

    async def application_list(self, *, material_mds: str, component_struts_id: str,
                               parent_component_mds: str) -> list:
        """Per-substance application rows of one attached Material."""
        rows = await self._post("/api/mds/tree/getApplyList", {
            "materialMdsId": material_mds,
            "comCsid": component_struts_id,
            "pComMdsId": parent_component_mds,
        })
        return rows or []

    async def application_standards(self, *, material_classification: str, material_mds: str,
                                    substance_id: str) -> tuple[str, list]:
        """The application options CAMDS offers for one substance, and the current one.

        Options depend on the substance and its material classification, so they
        are read here rather than kept in a table.
        """
        found = await self._post("/api/mds/tree/getApplyAppstd", {
            "matClsId": material_classification,
            "materialMdsId": material_mds,
            "subId": substance_id,
        }) or {}
        return str(found.get("selectedOption") or ""), found.get("applyViewList") or []

    async def set_application(self, row: dict, option: dict, *, material_mds: str) -> None:
        """Record one chosen application against one substance row.

        `row` comes from `application_list`, `option` from
        `application_standards`; the write carries the row's identity plus the
        option's `optionCode` and `appstdid`.
        """
        for field, source in (("optionCode", option), ("appstdid", option), ("subId", row)):
            if not source.get(field):
                raise CamdsApiError(f"Cannot apply an application without {field}")
        await self._post("/api/mds/tree/addOrUpdateApply", payload={
            "subId": row["subId"], "subTypeId": row.get("subTypeId"),
            "cid": material_mds, "prtstrid": row.get("prtstrid"),
            "name": row.get("name"), "enName": row.get("enName"),
            "maxRate": row.get("maxRate"), "option": row.get("option"),
            "enOption": None, "mcid": row.get("mcid"), "rateType": None,
            "appstdid": option["appstdid"], "optionCode": option["optionCode"],
            "_t": _stamp(),
        })

    async def material_classifications(self) -> list:
        return await self._post(
            "/api/dataTransform/materialClassification/getMaterialClassificationList",
            payload={"_t": _stamp()}) or []
