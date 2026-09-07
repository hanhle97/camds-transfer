# Releasing a Material — evidence from `release_material.har`

26 API calls, recorded releasing `CA_8_55110193`. Publishing is outward-facing
and cannot be undone from here, so this file records what was observed rather
than what seemed reasonable.

## The steps that matter

| Method | Path | Query | Purpose |
| --- | --- | --- | --- |
| POST | `/api/mds/mdsValidate/mdsValidate` | — | CAMDS's own check; `errorSize` is the gate |
| POST | `/api/mds/material/editMaterialRecyclateVO` | — | the recyclate question |
| POST | `/api/mds/supplier/getMdsCreator` | `mdsId` | who is signed in |
| POST | `/api/mds/supplier/findMDSContacterViewList` | `orgId` | that organisation's contacts |
| POST | `/api/mds/supplier/saveSupplierDataView` | `mdsid`, `supplierContactId`, `orgId`, `userId` | name the contact |
| POST | `/api/mds/mdsValidate/innerPublish` | `mdsId` | **release** |

## The order is part of the evidence

Going straight to `editMaterialRecyclateVO` is refused with the generic
`程序异常` even carrying the whole record. The form opens the MDS first, and
that prefix is not decoration:

```
canbeModifyMx        mdsId          announce the intent to modify
loadMdsTree          mdsId
loadNodeDate         strutsId
bStandardMaterials   mdsId          answered false
mdsValidate                         errorSize 1 - the question is unanswered
loadNodeDate         strutsId
bStandardMaterials   mdsId
editNodeDate                        the node posted back unchanged
editMaterialRecyclateVO             only now
```

Which single call CAMDS actually requires is not known. All of them are made,
in that order, which is the same discipline that fixed `getMdsStatus` before
`loadMdsTree`.

## Validation is the gate

Recorded twice, and the two answers are the point:

```
before the recyclate answer   errorSize: 1   errorFlag: "1"
after it                      errorSize: 0   errorFlag: "0"
```

A Material CAMDS reports errors on is not published. The first call is the form
opening; only the one before publishing decides, so only that one is made here.

## The recyclate answer

`containRecyclate: 2` is No. **All 29 fields go, nulls included** - sending
only the five that carry a value was answered with the generic `程序异常`, the
same way a null `cindex` and a null `recycledmaterials` were. CAMDS wants the
whole record, not the difference. The five with values:

```json
{"containRecyclate": 2,
 "inorganicFossilBasedMinrate": 100, "inorganicFossilBasedMaxrate": 100,
 "bioBasedMinrate": 0, "bioBasedMaxrate": 0}
```

Wholly inorganic fossil based, no bio content, which is what the form fills in
when the answer is No.

## Nothing about the operator is configured

`getMdsCreator` answers with the signed-in user, and that is where both ids
come from:

```json
{"userId": "CA_2_111138", "enterprsieId": "CA_3_3386", "loginName": "...", "name": "..."}
```

`supplierContactId` is the `scid` of the entry in
`findMDSContacterViewList(orgId)` whose `userId` equals that `userId` — the
signed-in person's own contact, not the first row. Two matches or none stops
the release rather than choosing.

A build handed to somebody else therefore releases as them. Nothing here is
hardcoded, and `CA_2_111138` / `CA_3_3386` appear only as the recorded example.

## Not evidenced

Releasing anything but a Material. Withdrawing a release. What `bStandardMaterials`
is for — it is called three times and answered `false` every time, and the
release does not appear to depend on it.
