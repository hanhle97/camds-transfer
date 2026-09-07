"""Run the tree import over the CAMDS JSON API instead of the browser DOM.

`TreeImporter` is unchanged: the journal, progress, pause/stop, resume and the
substance merging all stay where they are. Only the layer that touches CAMDS is
replaced, so both backends answer the same eighteen calls.

The two speak different languages, and this class is the translator:

* the importer addresses nodes by a path of display names plus an occurrence,
  because that is what a tree in a browser offers;
* the API addresses them by `strutsId`, and returns one when a node is created.

So every node created or read back is remembered under its path, and a label
such as "Measured Mass per Item" is mapped to the field that actually carries
it. Where a value lives on the parent relation rather than on the node - mass,
quantity and every portion - it is written to `structureVO`.

Evidence for every endpoint and field is in `CREATE_COMPONENT_API.md`.
"""
from __future__ import annotations

from .api import (CLASSIFICATION, FIXED, FROM_TO, MASS_PER_ITEM, MATERIAL_NODE, NAME,
                  RECYCLATE_NONE,
                  NODE_CAS, NODE_NAME,
                  NUMBER, REL_MASS, REL_MASS_UNIT, REL_MODE, REL_QUANTITY, REL_RATE, REST,
                  SEARCH_CAS, SEARCH_ID,
                  SEARCH_NAME, WEIGHT_UNIT, CamdsApiError, TreeNode, addressable, number,
                  portion)
from .import_plan import proportion, real_cas
from .substance_mapping import SubstanceMapping
from .material_classifications import classification_code

# What the importer asks for, and where CAMDS keeps it. A value that describes
# how a node sits inside its parent lives on the relation, not on the node.
FIELDS = {
    "Article Name": ("data", NAME),
    "Material Name": ("data", NAME),
    "Component No.": ("data", NUMBER),
    "Material No.": ("data", NUMBER),
    "Semicomponent No.": ("data", NUMBER),
    "Measured Mass per Item": ("data", MASS_PER_ITEM),
    "Quantity": ("structureVO", REL_QUANTITY),
    "Mass": ("structureVO", REL_MASS),
}

PORTION_MODES = {"fixed": FIXED, "range": FROM_TO, "rest": REST}


def _offered(rows, limit=6) -> str:
    """Name what the catalogue did return, so the operator can act on it."""
    if not rows:
        return ("CAMDS returned no rows at all for that search, so the catalogue "
                "appears not to hold it under that identifier.")
    shown = "; ".join(
        f"{r.get(SEARCH_NAME) or r.get('name') or '?'} (CAS {r.get(SEARCH_CAS) or 'none'}, "
        f"id {r.get(SEARCH_ID) or '?'})" for r in rows[:limit])
    more = f" and {len(rows) - limit} more" if len(rows) > limit else ""
    return f"CAMDS offered {len(rows)} row(s): {shown}{more}."


def named(value) -> dict:
    """Only write a number when the report actually carries one."""
    return {NUMBER: value} if value else {}


class ApiBackend:
    def __init__(self, api, substances=None, first_row_when_unclear=True) -> None:
        self.api = api
        # Choices a person has already made, and the ones this run ran into.
        self.substances = substances if substances is not None else SubstanceMapping()
        self.pending: list[tuple[dict, list]] = []
        # When the catalogue does not identify one substance, take the first row
        # it offers instead of stopping. Set by instruction; every use is
        # recorded with the rows it chose between and reported with the run.
        self.first_row_when_unclear = first_row_when_unclear
        self.reporter = None
        self.root = None                 # TreeNode of the tree being built or read
        self.current: str | None = None  # strutsId the importer last selected
        self.view: dict | None = None    # its loaded view
        self._paths: dict[tuple, list[str]] = {}   # path -> strutsIds, in tree order
        self._children: dict[str, list[str]] = {}  # strutsId -> its children, in order
        self._classification: dict[str, str] = {}  # strutsId -> material classification
        # The Material currently being built, and the substances put into it.
        self._material: tuple[str, str, str] | None = None
        self._substances: dict[str, str] = {}
        # Nodes that point at another MDS; CAMDS is asked about one before it
        # will serve that node, and about a Material again before its substances.
        self._referenced: dict[str, str] = {}
        # Read-back differences that are accepted but reported, not failures.
        self.findings: list[str] = []
        self._kind: dict[str, int | None] = {}   # strutsId -> nodeType
        self._text: dict[str, str] = {}          # strutsId -> label CAMDS shows
        self._cas: dict[str, str | None] = {}    # strutsId -> CAS, for Substances
        self._mds: dict[str, str | None] = {}    # strutsId -> the MDS it points at

    # ------------------------------------------------------------- bookkeeping
    def _remember(self, path: tuple, struts_id: str) -> None:
        self._paths.setdefault(tuple(path), []).append(struts_id)

    def _resolve(self, path, at=(0, 1)) -> str:
        occurrence, total = at
        found = self._paths.get(tuple(path), [])
        if len(found) != total or occurrence >= len(found):
            raise CamdsApiError(
                f"{' / '.join(path)}: expected {total} node(s), CAMDS has {len(found)}")
        return found[occurrence]

    def _adopt(self, parent_struts_id: str, child_struts_id: str) -> None:
        self._children.setdefault(parent_struts_id, []).append(child_struts_id)

    def _within(self, parent_struts_id: str) -> dict:
        """How a recorded child edit identifies itself: its parent and siblings."""
        return {"parent_id": parent_struts_id,
                "brothers": tuple(self._children.get(parent_struts_id, []))}

    def _next_index(self, parent_struts_id: str) -> int:
        return len(self._children.get(parent_struts_id, []))

    def _map_tree(self, node: dict, path: tuple = ()) -> None:
        """Index a tree returned by loadMdsTree so paths resolve again.

        A node that points at another MDS arrives under a prefixed id, so the
        addressable one is what is indexed; `crefFlag` marks it, and it marks a
        referenced Component as well as a referenced Material.
        """
        here = path + (node.get("text") or "",)
        struts_id = addressable(node["id"])
        self._remember(here, struts_id)
        self._children[struts_id] = [addressable(child["id"])
                                     for child in node.get("children") or []]
        self._kind[struts_id] = node.get("nodeType")
        # CAMDS may label a saved substance in either language, so the English
        # name is what a resumed run compares - the same field read-back uses.
        self._text[struts_id] = node.get("cenName") or node.get("text") or ""
        # loadMdsTree carries the CAS, so a saved composition can be read back
        # and reconciled without loading every node.
        self._cas[struts_id] = node.get("cascode")
        self._mds[struts_id] = node.get("mdsId")
        if node.get("cmatClsId"):
            self._classification[struts_id] = node["cmatClsId"]
        if (node.get("rf") or str(node.get("crefFlag") or "") == "1") and node.get("mdsId"):
            self._referenced[struts_id] = node["mdsId"]
        for child in node.get("children") or []:
            self._map_tree(child, here)

    async def _load(self, struts_id: str) -> dict:
        """Read one node, asking what CAMDS asks before it will serve it."""
        referenced = self._referenced.get(struts_id)
        if referenced:
            await self.api.can_modify(referenced)
        self.current = struts_id
        self.view = await self.api.load_view(struts_id)
        if referenced and self._kind.get(struts_id) == MATERIAL_NODE:
            # The browser asks this between a referenced Material and its
            # substances; without it their reads are refused.
            await self.api.is_standard_material(referenced)
        return self.view

    # ------------------------------------------------------------------ create
    async def prepare(self) -> None:
        """Prove the API session works before anything is created.

        The importer calls this before it opens a journal and before CAMDS
        allocates any id, so a session that has lapsed costs nothing here: the
        run stops with no draft and no journal to reconcile. A read-only call is
        enough, because an expired session answers the login page rather than
        JSON.
        """
        try:
            # A search is read-only and is a call the import itself depends on.
            await self.api.find_material(name='__camds_api_session_probe__')
        except CamdsApiError as exc:
            raise CamdsApiError(
                "The CAMDS API refused a read before anything was created "
                f"({exc}). Sign in again in the browser window, then retry; "
                "nothing was allocated.") from exc

    async def find_existing_material(self, node) -> tuple[str, str] | None:
        """The Material already in CAMDS that this node describes, if any.

        Creating one per run is what filled the account with duplicates. Reusing
        needs certainty about identity, and the search rows do not carry a name:
        they carry `mdsId`, `symbol` and `version`. So the Material No. is the
        key, and the name is confirmed afterwards by reading the saved tree.

        Only whole-numbered versions count. A version like 0.01 is a draft that
        somebody, possibly this tool, left half-built; 1, 2, 6 are the released
        ones. Reusing a draft would attach an unfinished composition.

        Returns None whenever identity is not certain, and the caller creates a
        new Material. A duplicate is a nuisance; the wrong composition attached
        to a part is wrong data.
        """
        number = str(node.get("material_number") or "").strip()
        if not number:
            return None  # nothing that identifies it; a name search cannot confirm
        rows = await self.api.find_material(symbol=number)
        released = []
        for row in rows:
            if str(row.get("symbol") or "").strip() != number:
                continue
            version = str(row.get("version") or "").strip()
            if version.isdigit() and row.get("mdsId"):
                released.append((int(version), str(row["mdsId"])))
        wanted = str(node.get("name") or "").strip().casefold()
        for version, mds_id in sorted(released, reverse=True):
            tree = await self.api.load_tree(mds_id)
            if str(tree.get("text") or "").strip().casefold() == wanted:
                return mds_id, str(version)
        return None

    async def find_existing_component(self, node, resolved) -> tuple[str, str] | None:
        """The Component already in CAMDS that this node is, if any.

        Three things have to agree, by instruction: the Part No., the number of
        children, and every child pointing at the MDS this run resolved for it.
        Anything else and a new Component is created.

        `resolved` maps each child's uid to the MDS id it ended up as, so this
        can only be asked once the children are known - the subtree is matched
        from the bottom up.

        A structure is not a name, so no name check: the same part may be
        called something slightly different in CAMDS and still be the same
        assembly of the same references. What must not differ is what it is
        made of.
        """
        number = str(node.get("part_number") or "").strip()
        if not number:
            return None  # nothing that identifies it
        wanted = []
        for child in node.get("children") or []:
            mds = resolved.get(child.get("uid"))
            if not mds:
                return None  # a child this run has not resolved cannot be compared
            wanted.append(str(mds))

        rows = await self.api.find_component(symbol=number)
        released = []
        for row in rows:
            if str(row.get("symbol") or "").strip() != number:
                continue
            version = str(row.get("version") or "").strip()
            if version.isdigit() and row.get("mdsId"):
                released.append((int(version), str(row["mdsId"])))
        for version, mds_id in sorted(released, reverse=True):
            tree = await self.api.load_tree(mds_id)
            children = tree.get("children") or []
            if len(children) != len(wanted):
                continue
            if all(str(c.get("mdsId")) == expect for c, expect in zip(children, wanted)):
                return mds_id, str(version)
        return None

    async def add_component_reference(self, parent_path, child, ref, at=(0, 1)) -> str:
        """Attach a Component that already exists, rather than building it again.

        The same call that attaches a Material: `substituteMdsNode` takes an
        `mdsId` and does not ask what kind it is, and a saved tree does come
        back holding referenced Components (`ref1_-CA_21_594394588`, nodeType 1).
        That the call was only ever recorded attaching a Material is the part
        of this that is inference rather than evidence.
        """
        parent = self._resolve(parent_path, at)
        attached = await self.api.attach_mds(
            root_struts_id=self.root.struts_id, root_mds=self.root.mds_id, mds_id=ref[0],
            parent_struts_id=parent, index=self._next_index(parent))
        self._adopt(parent, attached.struts_id)
        await self._load(attached.struts_id)
        display = (self.view["data"] or {}).get(NAME) or attached.text
        self._remember(tuple(parent_path) + (display,), attached.struts_id)
        await self.api.set_relation(attached.struts_id,
                                    {REL_QUANTITY: number(child["quantity"])},
                                    **self._within(parent))
        await self._load(attached.struts_id)
        return display

    async def create_root(self, node, on_allocated=None, existing=None) -> tuple[str, str]:
        """Allocate the MDS, then fill it.

        `on_allocated` is called the moment CAMDS issues the id, before any
        field is written. A failure while filling would otherwise leave an id
        consumed in CAMDS that no journal can name.

        `existing` reopens an MDS an earlier run already allocated and fills it
        again instead of spending a second id. The fields are written the same
        way either way, so a run interrupted before or during the fill ends up
        in the same state as one that never stopped.
        """
        if existing is not None:
            material = node["node_type"] == "MATERIAL"
            kind = "Material" if material else "Component"
            await self.open_saved(kind, existing)
            fields = ({NAME: node["name"], **named(node.get("material_number"))} if material else
                      {NAME: node["name"], **named(node.get("part_number")),
                       MASS_PER_ITEM: number(node["weight_g"]), WEIGHT_UNIT: "g"})
            await self.api.set_fields(self.root.struts_id, fields)
            # Read the tree again so paths are indexed under the name now
            # stored: a run interrupted during the fill left the old label, and
            # every later step addresses this node by the name being imported.
            await self.open_saved(kind, existing)
            self._substances = {}
            self._material = ((self.root.struts_id, self.root.mds_id,
                               classification_code(node.get("classification")))
                              if material else None)
            return self.root.reference
        if node["node_type"] == "MATERIAL":
            code = classification_code(node.get("classification"))
            created = await self.api.create_material_root(code)
            self._classification[created.struts_id] = code
            self._material = (created.struts_id, created.mds_id, code)
            self._substances = {}
            fields = {NAME: node["name"], **named(node.get("material_number"))}
        else:
            created = await self.api.create_component_root()
            self._material, self._substances = None, {}
            fields = {NAME: node["name"], **named(node.get("part_number")),
                      MASS_PER_ITEM: number(node["weight_g"]), WEIGHT_UNIT: "g"}
        self.root = created
        if on_allocated:
            on_allocated(created.reference)
        self._paths, self._children = {}, {}
        self._kind, self._referenced = {}, {}
        self._remember((node["name"],), created.struts_id)
        await self.api.set_fields(created.struts_id, fields)
        await self._load(created.struts_id)
        return created.reference

    async def save(self) -> None:
        await self.api.save(self.root.struts_id, self.root.mds_id)

    def _reuse(self, parent: str, index: int | None) -> str | None:
        """The child already at this position, for a resumed run to fill again.

        A run interrupted between creating a node and writing its fields leaves
        it in the tree unnamed. Filling that one again is what lets a resume
        finish, instead of adding a second node beside it.
        """
        if index is None:
            return None
        children = self._children.get(parent, [])
        if index >= len(children):
            raise CamdsApiError(f"CAMDS holds no child at position {index} to fill")
        return children[index]

    async def add_component(self, parent_path, child, at=(0, 1), reuse_index=None) -> None:
        parent = self._resolve(parent_path, at)
        struts_id = self._reuse(parent, reuse_index)
        if struts_id is None:
            created = await self.api.add_component(
                self.root.mds_id, parent, self._next_index(parent))
            struts_id = created.struts_id
            self._adopt(parent, struts_id)
        self._remember(tuple(parent_path) + (child["name"],), struts_id)
        await self.api.set_fields(struts_id, {
            NAME: child["name"], **named(child.get("part_number")),
            MASS_PER_ITEM: number(child["weight_g"]), WEIGHT_UNIT: "g",
            # A number typed into a form reaches CAMDS as a string.
        }, relation={REL_QUANTITY: number(child["quantity"])}, **self._within(parent))
        await self._load(struts_id)

    async def add_semicomponent(self, parent_path, child, at=(0, 1), by_portion=False,
                                reuse_index=None) -> None:
        """Insert a Semicomponent, declared by mass or - inside another
        Semicomponent - by portion, which is how the report declares it."""
        parent = self._resolve(parent_path, at)
        struts_id = self._reuse(parent, reuse_index)
        if struts_id is None:
            created = await self.api.add_semicomponent(
                self.root.mds_id, parent, self._next_index(parent))
            struts_id = created.struts_id
            self._adopt(parent, struts_id)
        self._remember(tuple(parent_path) + (child["name"],), struts_id)
        # Mass and portion both live on the parent relation, as for a Material.
        relation = (portion(*self._portion(child)) if by_portion
                    else {REL_MASS: number(child["weight_g"]), REL_MASS_UNIT: "g"})
        await self.api.set_fields(struts_id, {
            NAME: child["name"], **named(child.get("part_number")),
        }, relation=relation, **self._within(parent))
        await self._load(struts_id)

    async def add_material(self, parent_path, node, ref, at=(0, 1), by_portion=False,
                           reuse_index=None) -> str:
        parent = self._resolve(parent_path, at)
        struts_id = self._reuse(parent, reuse_index)
        if struts_id is None:
            attached = await self.api.attach_mds(
                root_struts_id=self.root.struts_id, root_mds=self.root.mds_id, mds_id=ref[0],
                parent_struts_id=parent, index=self._next_index(parent))
            struts_id = attached.struts_id
            self._adopt(parent, struts_id)
        await self._load(struts_id)
        display = (self.view["data"] or {}).get(NAME) or self._text.get(struts_id, "")
        self._remember(tuple(parent_path) + (display,), struts_id)
        if by_portion:
            relation = portion(*self._portion(node))
        else:
            relation = {REL_MASS: number(node["weight_g"]), REL_MASS_UNIT: "g"}
        await self.api.set_relation(struts_id, relation, **self._within(parent))
        await self._load(struts_id)
        return display

    @staticmethod
    def _portion(node) -> tuple:
        mode = proportion(node)
        return (PORTION_MODES[mode[0]], *mode[1:])

    async def add_substance(self, material_name, node) -> str:
        parent = self._resolve((material_name,))
        found = await self.resolve_substance(node)
        created = await self.api.add_substance(
            self.root.mds_id, parent, str(found[SEARCH_ID]), self._next_index(parent))
        self._adopt(parent, created.struts_id)
        display = found.get(SEARCH_NAME) or node["name"]
        self._remember((material_name, display), created.struts_id)
        self._substances[display] = created.struts_id
        await self.api.set_relation(created.struts_id, portion(*self._portion(node)),
                                    **self._within(parent))
        await self._load(created.struts_id)
        return display

    async def resolve_substance(self, node) -> dict:
        """One exact catalogue match, by CAS when there is one and by name otherwise.

        A substance is the composition of its Material, so an unresolved one
        stops the run rather than being skipped: a Material missing a portion of
        itself is wrong data, not incomplete data. The message carries what
        CAMDS actually offered so the operator can tell a naming difference from
        a substance the catalogue does not hold.
        """
        cas = real_cas(node)
        wanted = str(node.get("name") or "").strip().casefold()
        if cas:
            rows = await self.api.find_substance(cas=cas)
            hits = [r for r in rows if str(r.get(SEARCH_CAS) or "").strip() == cas]
            if len(hits) > 1:
                # Several rows share the CAS under different names. The report
                # names one of them, and that is the report's own evidence, not
                # a preference of ours: "7440-21-3" is offered as "P-SI" and as
                # "Silicon", and the substance being imported is called Silicon.
                named = [r for r in hits if str(r.get(SEARCH_NAME) or "").strip().casefold() == wanted]
                if len(named) == 1:
                    return named[0]
            criterion = f"CAS {cas}"
        else:
            rows = await self.api.find_substance(name=node["name"])
            hits = [r for r in rows if str(r.get(SEARCH_NAME) or "").strip().casefold() == wanted]
            criterion = f"name {node['name']!r}"
        if len(hits) == 1:
            return hits[0]
        chosen = self.substances.chosen(node)
        if chosen:
            picked = [r for r in rows if str(r.get(SEARCH_ID)) == chosen]
            if len(picked) == 1:
                return picked[0]
            raise CamdsApiError(
                f"{node['name']}: the recorded choice {chosen} is no longer one of the "
                f"{len(rows)} row(s) CAMDS offers. " + _offered(rows))
        if self.first_row_when_unclear and rows:
            # By instruction: take the first row CAMDS offers rather than stop.
            # Recorded and reported on every use - the choice is the software's,
            # so it must never be mistaken for one a person made.
            picked = (hits or rows)[0]
            self.substances.auto(node, picked, rows)
            self.findings.append(
                f"{node['name']}: {criterion} did not identify one substance, so the first of "
                f"{len(hits) or len(rows)} row(s) CAMDS offered was used - "
                f"{picked.get(SEARCH_NAME)!r} (id {picked.get(SEARCH_ID)}). " + _offered(rows))
            return picked
        self.pending.append((node, rows))
        raise CamdsApiError(
            f"{node['name']}: searching the CAMDS substance catalogue by {criterion} "
            f"matched {len(hits)} entries exactly, not one. " + _offered(rows))

    # ------------------------------------------------------------------ release
    async def release_material(self, ref) -> tuple[str, str]:
        """Publish a Material. Outward-facing, and not reversible from here.

        The steps are the release form's own, from `release_material.har`:
        answer the recyclate question, save the node, name the supplier contact,
        let CAMDS validate, and publish. Validation is the gate - it reported
        one error before the recyclate answer and none after - so a Material
        CAMDS is not satisfied with is never published.

        Nothing about the operator is configured. `getMdsCreator` says who is
        signed in, and the contact is that same person's entry in their
        organisation's list, so a build handed to somebody else releases as
        them and not as whoever recorded this.
        """
        mds_id = ref[0]
        # The form's opening sequence, in its order. Jumping straight to the
        # recyclate write was refused with a generic "程序异常" even with the
        # whole record: CAMDS wants the MDS announced and opened first, the way
        # it wants getMdsStatus before a tree is read.
        await self.api.can_modify(mds_id)
        await self.open_saved("Material", ref)
        await self.api.b_standard_materials(mds_id)
        await self.api.validate_mds(mds_id)
        await self._load(self.root.struts_id)
        await self.api.b_standard_materials(mds_id)
        await self.api.set_fields(self.root.struts_id, {})

        await self.api.set_material_recyclate(mds_id, self.root.struts_id,
                                              dict(RECYCLATE_NONE))
        await self._load(self.root.struts_id)
        await self.api.set_fields(self.root.struts_id, {})
        await self.api.save(self.root.struts_id, mds_id)

        creator = await self.api.mds_creator(mds_id)
        user_id, org_id = creator.get("userId"), creator.get("enterprsieId")
        if not user_id or not org_id:
            raise CamdsApiError(
                f"{mds_id}: CAMDS did not say who is signed in, so the release cannot "
                "name a supplier contact")
        contacts = await self.api.org_contacts(org_id)
        mine = [c for c in contacts if str(c.get("userId")) == str(user_id) and c.get("scid")]
        if len(mine) != 1:
            raise CamdsApiError(
                f"{mds_id}: {len(mine)} contact(s) in organisation {org_id} belong to the "
                f"signed-in user {user_id}, not one; the release cannot choose for them")
        await self.api.save_supplier_contact(mds_id=mds_id, contact_id=str(mine[0]["scid"]),
                                             org_id=str(org_id), user_id=str(user_id))

        checked = await self.api.validate_mds(mds_id)
        errors = int(checked.get("errorSize") or 0)
        if errors:
            raise CamdsApiError(
                f"{mds_id}: CAMDS reports {errors} validation error(s), so it was not "
                "released. Open it in CAMDS to see them.")
        await self.api.publish_mds(mds_id)
        # Releasing bumps the version: the 0.01 draft becomes 1. Everything
        # after this addresses the Material by id *and* version - attaching it,
        # and reading the tree back - so the new one is what the caller gets.
        await self.open_saved("Material", (mds_id, ref[1]))
        return self.root.reference

    # ------------------------------------------------------------- read / verify
    async def open_saved(self, kind, ref) -> None:
        """Re-read a saved MDS.

        CAMDS asks for the MDS status before it will serve the tree; without
        that call the nodes of the tree it returns cannot be read back, and
        `loadNodeDate` answers with a generic program exception.
        """
        if kind == "Material":
            await self.api.material_status(ref[0])
        else:
            await self.api.mds_status(ref[0])
        tree = await self.api.load_tree(ref[0])
        # Always rebuild the root: resuming opens a saved MDS this backend has
        # not created, and everything added afterwards addresses root.mds_id.
        self.root = TreeNode.from_payload(tree)
        self._paths, self._children, self._classification = {}, {}, {}
        self._referenced, self._kind = {}, {}
        self._text, self._cas, self._mds = {}, {}, {}
        self._map_tree(tree)
        await self._load(addressable(tree["id"]))

    async def can_reenter_saved(self) -> bool:
        """A saved MDS can be reopened and added to, so a run can be resumed."""
        return True

    async def saved_children(self, path, at=(0, 1)) -> list[dict]:
        """What CAMDS already holds under a node, from the tree it just served.

        Reconciling a resumed run against CAMDS rather than against the journal
        is what keeps it from adding a second copy of something that a failure
        interrupted after the write but before the journal entry.
        """
        parent = self._resolve(path, at) if path else self.current
        return [{"name": self._text.get(child, ""), "cas": self._cas.get(child),
                 "mds": self._mds.get(child)}
                for child in self._children.get(parent, [])]

    async def select(self, path, at=(0, 1)) -> None:
        await self._load(self._resolve(path, at))

    async def identity(self) -> tuple[str, str]:
        node = (self.view or {}).get("treeDataNode") or {}
        version = node.get("mdsCver")
        if not node.get("mdsId") or version is None:
            raise CamdsApiError("The selected node has no MDS id and version")
        return node["mdsId"], format(float(version), ".12g")

    async def value(self, label) -> str:
        section, field = FIELDS[label]
        return str(((self.view or {}).get(section) or {}).get(field) or "")

    async def verify_value(self, label, expected) -> None:
        section, field = FIELDS[label]
        actual = ((self.view or {}).get(section) or {}).get(field)
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            import math
            if actual is None or not math.isclose(float(actual), float(expected),
                                                  rel_tol=1e-8, abs_tol=1e-8):
                raise CamdsApiError(f"Read-back mismatch: {label}: {actual} != {expected}")
        elif str(actual or "") != str(expected or ""):
            raise CamdsApiError(f"Read-back mismatch: {label}: {actual!r} != {expected!r}")

    async def verify_child_count(self, path, count, at=(0, 1)) -> None:
        struts_id = self._resolve(path, at)
        actual = len(self._children.get(struts_id, []))
        if actual != count:
            raise CamdsApiError(
                f"{' / '.join(path)}: CAMDS holds {actual} child node(s), not {count}")

    async def verify_substance(self, path, node, at=(0, 1)) -> None:
        """Find the saved Substance among its parent's children.

        CAMDS may label a substance in either language once saved, so the stable
        CAS decides; a system group has none and is matched on its English name.

        That name is the catalogue's, not the report's - `path[-1]` is what
        attaching returned. They differ whenever the catalogue holds the
        substance under another name, and comparing the report's name would
        then look for something that was never written.
        """
        cas = real_cas(node)
        parent = self._resolve(path[:-1])
        wanted = str(path[-1]).strip().casefold()
        for candidate in self._children.get(parent, []):
            record = (await self._load(candidate)).get("data") or {}
            if cas:
                if str(record.get(NODE_CAS) or "").strip() == cas:
                    return await self.verify_proportion(node)
            elif str(record.get(NODE_NAME) or "").strip().casefold() == wanted:
                return await self.verify_proportion(node)
        raise CamdsApiError(
            f"Saved Substance {'CAS ' + cas if cas else repr(node['name'])} is missing")

    async def read_back_findings(self) -> list[str]:
        """Differences the read-back accepted, for the operator to judge."""
        found, self.findings = list(self.findings), []
        return found

    async def verify_proportion(self, node, what="Substance") -> None:
        """Check the portion CAMDS saved against the one the report declares.

        Rest is checked as a mode and not as a number. "Rest" means whatever the
        siblings leave over, so CAMDS computes the value itself; the figure IMDS
        prints beside it - "Rest 7.98" - describes the same remainder rather than
        instructing one, and demanding it back is asking CAMDS to agree with an
        arithmetic it did not perform.
        """
        expected = portion(*self._portion(node))
        relation = (self.view or {}).get("structureVO") or {}
        if str(relation.get(REL_MODE)) != str(expected[REL_MODE]):
            raise CamdsApiError(f"Saved {what} portion mode: {relation.get(REL_MODE)} "
                                f"!= {expected[REL_MODE]}")
        if expected[REL_MODE] == REST:
            return self._compare_rest(node, relation, what)
        for field, wanted in expected.items():
            actual = relation.get(field)
            if isinstance(wanted, (int, float)):
                if actual is None or abs(float(actual) - float(wanted)) > 1e-8:
                    raise CamdsApiError(f"Saved {what} proportion mismatch: {field} "
                                        f"{actual} != {wanted}")
            elif str(actual) != str(wanted):
                raise CamdsApiError(f"Saved {what} proportion mismatch: {field}")

    def _compare_rest(self, node, relation, what) -> None:
        """Report, but do not refuse, a remainder that differs from the report's.

        A difference here means the siblings do not add up the way the report
        says they do. That is the operator's judgement to make - the declared
        composition is what it is - so it is recorded and handed back with the
        result rather than stopping a run that CAMDS accepted.
        """
        printed, computed = node.get("percentage"), relation.get(REL_RATE)
        if printed is None or computed is None:
            return
        difference = abs(float(computed) - float(printed))
        if difference > 0.01:
            self.findings.append(
                f"{node.get('name')}: the report prints Rest {printed}, CAMDS computed "
                f"{computed} from the other portions (difference {difference:.4g}). "
                "The remainder CAMDS holds is the one it calculated.")

    # ------------------------------------------------------------- applications
    async def application_options(self, substance_name):
        """The options CAMDS offers for one substance of the Material just built.

        Returned in the shape the importer expects: a handle, and options each
        carrying a `value` and a `label`. Options depend on the substance *and*
        the material classification, so they are read per substance.
        """
        struts_id = self._substances.get(substance_name)
        if struts_id is None or self._material is None:
            return {"options": {}}, []
        record = (await self.api.load_view(struts_id)).get("data") or {}
        material_struts, material_mds, classification = self._material
        _, offered = await self.api.application_standards(
            material_classification=classification, material_mds=material_mds,
            substance_id=str(record.get("csubId") or ""))
        options = {str(o["optionCode"]): o for o in offered if o.get("optionCode")}
        handle = {"substance": record, "material_mds": material_mds,
                  "struts_id": material_struts, "options": options}
        return handle, [{"value": code, "label": option.get("enOption")}
                        for code, option in options.items()]

    async def apply_application(self, handle, resolution) -> None:
        option = handle["options"].get(str(resolution.value))
        if option is None:
            raise CamdsApiError(
                f"CAMDS no longer offers option {resolution.value} for this substance")
        record = handle["substance"]
        await self.api.set_application(
            {"subId": record.get("csubId"), "subTypeId": None,
             "prtstrid": handle["struts_id"], "name": record.get("cname"),
             "enName": record.get("cenName"), "maxRate": record.get("cratio"),
             "option": None, "mcid": None},
            option, material_mds=handle["material_mds"])

    async def cancel_application(self, handle) -> None:
        """Nothing was opened, so nothing has to be dismissed."""
