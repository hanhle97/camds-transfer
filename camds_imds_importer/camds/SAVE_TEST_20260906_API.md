# First complete import over the JSON API — 2026-09-06

Real data from `MDSReport_E_044200509K_69064_SAIC_1.pdf`, one branch of the
8052-node tree. Parsed, planned, created, saved and read back with no browser
automation: the signed-in window supplied the session, every CAMDS call was
JSON. No Delete, Send, Submit, Publish, Release or Commitment action ran.

Journal:
`output/camds_imports/1e22d764…jsonl`, ending in `complete_readback_verified`.

## Created and verified

| Object | ID / version | Read back as |
|---|---|---|
| Material `VMQ` | CA_8_55100693 / 0.01 | 5 substances, portions as parsed |
| Component `Thermal Conduction Paste; TIM` | CA_5_158483804 / 0.01 | Material attached, mass from the report |

Read-back is CAMDS's own path — `getMdsStatus` / `getMaterialStatus`, then
`loadMdsTree`, then `loadNodeDate` per node — not a re-read of what we sent.

## Timing

30 seconds end to end (14:17:13 → 14:17:42), of which ~2 s per substance. The
Playwright backend took minutes per node.

## What this run proves, and what it does not

Proven: create Material, add catalogue Substances by CAS and by name, portions
(Fixed / From-To / Rest), create Component, attach a Material by reference,
save, and verify by reading the saved tree back.

Not exercised: `addOrUpdateApply` — this branch carries no application, so no
application has ever been written live. A branch with a real one (`48V pipe`)
is still the next test. Nested Semicomponents are also untested live.

## Fixes this run depended on

Two defects each ended an earlier run with the same generic `程序异常`, and
neither was visible in the message:

* a saved tree must be announced with `getMdsStatus` before its nodes are read;
* a node referencing another MDS is returned under `ref1_-CA_21_…` but is only
  ever addressed by the bare `CA_21_…`.

Both are recorded in `CREATE_COMPONENT_API.md` and replayed in
`tests/camds/recorded/`.
