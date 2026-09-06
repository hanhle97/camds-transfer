# CAMDS JSON API — recorded sessions, 2026-09-06

Source: seven HAR recordings of an operator working in CAMDS - create a
Component tree, create a Material, search Materials, search a Component and read
its tree.
Everything below is observed in that capture. Endpoints it does not contain are
listed at the end as gaps, not guessed.

Status: `camds/api.py` implements the observed calls. Reads are **verified live**
(see below); **no API write has been executed against CAMDS from this
application yet.**

## Origin and Referer are required — verified 2026-09-06

A request that is otherwise byte-identical to a recorded one is answered with
**HTTP 404** when `Origin` and `Referer` are missing. CAMDS is same-origin XHR
and its gateway appears to hide unrecognised callers behind a 404 rather than a
401 or 403, so the absent headers look like an absent endpoint.

With both headers present, two independent read-only calls succeeded on a
signed-in session:

- `findMaterialByCondition` answered a search,
- `getMaterialClassificationList` returned 55 classifications.

Every call therefore sends:

```
Origin:  https://catarc.camds.org.cn
Referer: https://catarc.camds.org.cn/
Accept:  application/json, text/plain, */*
```

The two informational headers the pages also send (`applogcontent`, `pagename`,
base64 UI labels) are **not** required.

## Authentication

`POST /api/login` sends an encrypted username and password plus a CAPTCHA:

```json
{"username": "<encrypted>", "password": "<encrypted>", "verifyCode": "8DRM", "_t": 1788665294}
```

The encryption happens in the page, and `verifyCode` comes from the CAPTCHA, so
there is no headless login. The session stays browser-established; API calls run
through the signed-in Playwright context and inherit its cookies. The capture
carries no bearer token and no `Authorization` header; two informational headers
(`applogcontent`, `pagename`, both base64 UI labels) are not required.

Every response is HTTP 200 even on refusal. The verdict is in the body:

```json
{"respCode": "0", "data": {...}, "ok": true, "message": ""}
```

## Identifiers

| Prefix | Meaning |
| --- | --- |
| `CA_21_*` | `strutsId` — a node's identity inside the open tree |
| `CA_5_*` | Component MDS id |
| `CA_7_*` | Semicomponent MDS id |
| `CA_8_*` | Material MDS id |
| `CA_3_*` | Organisation |

A node carries both: `id` is the `strutsId`, `mdsId` the MDS. Versions arrive as
numbers (`mdsCver: 0.01`).

## Write pattern

CAMDS replaces a whole record; it does not patch.

1. `POST /api/mds/tree/loadNodeDate?strutsId=…` → `data.view.data`, the record
2. mutate the fields
3. `POST /api/mds/tree/editNodeDate` with
   `{"brotherSidList": [], "editedStructId": …, "parentId": …, "view": {"data": …}}`
4. `POST /api/mds/tree/saveNodeDate?StrutsId=<root>&mdsId=<rootMds>` → `{"ok": true}`

Save addresses the **root**, not the edited node: one save persists the tree.

## Endpoints observed

| Method | Path | Query | Body | Purpose |
| --- | --- | --- | --- | --- |
| POST | `/api/mds/component/createInitComponent` | `mdsFlag=0` | `{_t}` | Allocate a Component MDS |
| POST | `/api/mds/component/addComponentNodeToTree` | `rootId`, `parentid`, `index` | `{_t}` | Add a child Component |
| POST | `/api/mds/semiComponent/addNewSemiComponentToTree` | `rootId`, `parentid`, `index` | `{_t}` | Add a Semicomponent |
| POST | `/api/mds/tree/substituteMdsNode` | `rootStrutsId`, `cblkid`, `mdsId`, `parentStrutsId`, `index` | `{_t}` | Reference an existing MDS |
| POST | `/api/mds/tree/loadNodeDate` | `strutsId` | `{_t}` | Read a node record |
| POST | `/api/mds/tree/editNodeDate` | — | record | Write a node record |
| POST | `/api/mds/tree/saveNodeDate` | `StrutsId`, `mdsId` | `{_t}` | Persist the tree |
| POST | `/api/mds/findMds/findMaterialByCondition` | — | search form | Find Materials |
| POST | `/api/mds/tree/getApplyList` | `materialMdsId`, `comCsid`, `pComMdsId` | `{_t}` | Application rows of a Material |
| POST | `/api/dataTransform/materialClassification/getMaterialClassificationList` | — | `{_t}` | Classification list |
| POST | `/api/mds/tree/isStandMaterial`, `/canbeModifyMx` | `mdsId` | `{_t}` | Editability checks |
| GET | `/api/mds/tree/getMaterialStatus/{mdsId}` | — | — | Material status |

## Record fields

| Table | Field | Meaning |
| --- | --- | --- |
| `t_component_node` | `cname` | Article Name |
| | `csymbol` | Component No. |
| | `cmeaWeightPerItem` | Measured Mass per Item, sent as a string (`"3000"`) |
| | `cweightUnit` | observed `"g"` |
| | `ccalWeightPerItem`, `cdeviation` | computed by CAMDS |
| `t_semi_component_node` | `cname`, `csymbol`, `cweight`, `cweightUnit` | Semicomponent fields |
| `t_material` | `cname`, `csymbol`, `cmatClsId` (e.g. `"5.3"`), `cid`, `cver` | Material record |

`getApplyList` returns one row per declarable substance:

```json
{"subId": "5744", "prtstrid": "CA_21_791936113", "name": "溶剂脱蜡重石蜡馏分",
 "enName": "distillates (petroleum), solvent-dewaxed heavy paraffinic",
 "maxRate": "18", "option": "<font color='blue'>none</font>", "optionCode": null}
```

`enName` is the English substance name the UI matches on, `optionCode` the chosen
application.

## Material creation and Substances

| Method | Path | Query | Purpose |
| --- | --- | --- | --- |
| POST | `/api/mds/material/createInitMaterial` | `mdsFlag=0`, `classification` | Allocate a Material MDS |
| POST | `/api/common/substance/findSubstanceByCondition` | — | Find a catalogue Substance |
| POST | `/api/mds/tree/addNewSubstanceToTree` | `rootMdsId`, `parentStrutsId`, `subId`, `index` | Attach one |
| POST | `/api/mds/tree/bStandardMaterials` | `mdsId` | Standard-material check |
| POST | `/api/common/substanceClassification/list` | — | Substance classification list |

**The classification is a query parameter.** `createInitMaterial?classification=3.1`
returns a node already carrying `cmatClsId: "3.1"`, so the browser's two-step
wizard - including the ISO 1043 symbol page that polymer classifications open -
is a UI construct the API does not impose.

Substances are attached by catalogue id, not by CAS:

```json
{"pageNo": 1, "pageSize": 10, "name": "", "cas": "7440-50-8",
 "prohibit": "3", "declare": "3", "svhc": "2"}
```

The result rows carry `csubId`, which `addNewSubstanceToTree` takes as `subId`.
A system group has the CAS placeholder `system` and is found by name instead -
"Misc., not to declare" is `csubId` 8172.

An attached Substance record holds `csubId`, `ccasCode`, `cenName`, `cname`
(Chinese), `cgadsl`, `csvhc` and **`cratio`**, the portion.

## Search and read-back

| Method | Path | Query | Purpose |
| --- | --- | --- | --- |
| POST | `/api/mds/findMds/findMaterialByCondition` | — | Find Materials |
| POST | `/api/mds/findMds/findComponentByCondition` | — | Find Components |
| GET | `/api/mds/tree/getMdsStatus/{mdsId}` | — | Announce a Component before reading it |
| GET | `/api/mds/tree/getMaterialStatus/{mdsId}` | — | Announce a Material before reading it |
| POST | `/api/mds/tree/loadMdsTree` | `mdsId` | **The whole saved tree** |
| POST | `/api/mds/tree/canbeModifyMx` | `mdsId` | Announce a referenced MDS before reading its node |

**Opening a saved MDS has an order.** The status call comes first, then the
tree, then its nodes:

```
GET  /api/mds/tree/getMdsStatus/CA_5_124767559
POST /api/mds/tree/loadMdsTree?mdsId=CA_5_124767559
POST /api/mds/tree/loadNodeDate?strutsId=CA_21_612736349
```

`loadMdsTree` answers without the status call, but `loadNodeDate` on a node of
that tree then fails with the generic `程序异常`. A node that references another
MDS is announced the same way, with `canbeModifyMx` for the referenced id, before
`loadNodeDate` is issued for it.

Both searches post the same form; `name` and `symbol` are the criteria:

```json
{"suppliesList": [], "ownerMds": true, "ownerUnit": true, "latestVer": false,
 "mdsApproved": false, "mdsPublished": false, "size": 10, "current": 1,
 "name": "Cu99", "symbol": "", "dateFrom": "2026-08-06", "dateto": "2026-09-06"}
```

The UI always sends a date window and defaults it to the **last month**, so a
caller looking for an older MDS has to widen it deliberately. No search by MDS
id appears in any capture; `loadMdsTree` addresses one directly instead, and
returns the root node with its `children`, which is the read-back path.

## The parent relation: portion, mass and quantity

`loadNodeDate` answers with the node record under `data` **and** the parent
relation under `structureVO`, and `editNodeDate` sends both back together in
`view`. Posting only the record drops the relation, and with it the portion,
mass and quantity, so the whole view always makes the round trip:

```
data, materialRecyclateVO, mdsState, refed, state, structState, structureVO, vocFlag
```

Two fields must not travel back as `null`, even though `loadNodeDate` answers a
freshly created node with them unset. CAMDS replies to either with a generic
`程序异常，请重试` — a server-side exception, not a validation message:

| Field | Rule from the recordings |
| --- | --- |
| `cindex` | never null in any write; a new root is sent as `0` |
| `recycledmaterials` | never null on a **Material** (`cnodeType` 3): a new Material was recorded as `2`, an attached reference as `0`. Components and Substances leave it null |

A write that changes a **child** node names its parent and its siblings:
`parentId` is the parent `strutsId` and `brotherSidList` lists the children of
that parent, the edited node included. Only a root is written with
`parentId: null` and an empty list.

Numbers the operator types reach CAMDS as **strings**: `cmeaWeightPerItem`
`"3000"`, `cweight` `"50"`, `cquantity` `"5"`. Sending them as JSON numbers is
answered with the same generic `程序异常`.

`structureVO` holds where a node sits and how much of it there is:

| Field | Meaning |
| --- | --- |
| `csid`, `cpsid`, `cblkid` | this node, its parent, the root MDS |
| `cckid`, `cindex`, `cnodeType` | child key, position, kind (1 Component, 3 Material, 4 Substance) |
| `cquantity` | **Component quantity** within the parent |
| `cweight`, `cweightUnit` | **Material mass** within a Component, in `g` |
| `crateType` | portion mode: **1 From-To, 2 Fixed, 3 Rest** |
| `crate` | the Fixed value, or the value a range resolves to |
| `cminRate`, `cmaxRate` | the From-To bounds |
| `residualRate` | computed by CAMDS |

The mode numbers are the same ones the browser radio inputs carry, so the two
paths agree. Observed rows:

| Node | `crateType` | fields |
| --- | --- | --- |
| MQ, a Substance declared 44-50% | 1 | `cminRate` 44, `cmaxRate` 50 |
| D6, a Substance declared 10% | 2 | `crate` 10 |
| Decamethylcyclopentasiloxane, the Rest | 3 | `crate` 0 |
| A Material under a Component | — | `cweight` 50, `cweightUnit` `g` |
| A child Component | — | `cquantity` 5 |

A Material inside a **Semicomponent** is declared by portion, like a Substance
inside a Material; a Material inside a **Component** carries a mass.

## Applications

| Method | Path | Query | Purpose |
| --- | --- | --- | --- |
| POST | `/api/mds/tree/getApplyList` | `materialMdsId`, `comCsid`, `pComMdsId` | The substance rows of an attached Material |
| POST | `/api/mds/tree/getApplyAppstd` | `matClsId`, `materialMdsId`, `subId` | The options offered for one substance |
| POST | `/api/mds/tree/addOrUpdateApply` | — | Record the chosen option |

`getApplyAppstd` answers with the current choice and the options:

```json
{"selectedOption": "27", "value": "200,0.1,27,…",
 "applyViewList": [{"enOption": "Concentration within acceptable GADSL limits",
                    "maxRate": "0.01", "appstdid": "1014", "optionCode": "27"},
                   {"enOption": "Other application (potentially prohibited)",
                    "maxRate": "0", "appstdid": "1061", "optionCode": "28"}]}
```

`enOption` is the English wording to match on. The write carries the row from
`getApplyList` plus **both** identifiers of the chosen option:

```json
{"subId": "2943", "cid": "CA_8_46123515", "prtstrid": "CA_21_791936664",
 "enName": "Lead", "maxRate": "0.02", "appstdid": "1061", "optionCode": "28"}
```

Options depend on the substance **and** the material classification, so they are
read per substance rather than kept in a table. The application itself comes
from the parsed report: its wording is matched against `enOption`, and anything
that does not match exactly is **left unset and reported**, never guessed. An
unset application is visibly missing in CAMDS; a wrong one is a false regulatory
statement that looks correct.

## Still not evidenced

- Nothing from the recorded flows remains unimplemented.
