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

## Validation is the gate

Recorded twice, and the two answers are the point:

```
before the recyclate answer   errorSize: 1   errorFlag: "1"
after it                      errorSize: 0   errorFlag: "0"
```

A Material CAMDS reports errors on is not published. The first call is the form
opening; only the one before publishing decides, so only that one is made here.

## The recyclate answer

`containRecyclate: 2` is No. 29 fields, 24 of them null:

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
