# CAMDS IMDS Importer

A Windows desktop application that converts native-text IMDS MDS Report PDFs into a canonical hierarchy, validates it, searches CAMDS, and transfers supported trees into saved CAMDS drafts with read-back verification. Send/Submit are not automated.

## Import a parsed tree into CAMDS

1. Parse the IMDS PDF and review the hierarchy, mass and quantity values.
2. Open **CAMDS Search / Create → Open CAMDS browser**. Sign in if needed and
   use English. This is the app's browser, separate from the Codex browser.
3. Click **Import parsed tree…** (or use the `IMPORT_TREE` workflow mode).
   Pick the node to start from in **Import starting at**. One unsupported branch
   no longer blocks the rest of a report: the review, the Material table and the
   preflight all follow the chosen node.
4. Review each Material. Leave ID/version blank to create a new Material, or
   enter an existing **CAMDS** ID and exact version. Existing Material composition
   is reused as-is, not rewritten from the parser. IMDS IDs are never substituted.
5. Click **Validate and preview**. Unsupported/missing data blocks the entire
   transfer before any ID allocation. Editing a mapping invalidates the preview.
6. Click **Create + Save tree in CAMDS** to start the reviewed plan.

### Two backends, one importer

The transfer runs over the **CAMDS JSON API** by default. `TreeImporter` itself
is unchanged - journal, progress, Pause/Stop, resume and substance merging all
stay put - and only the layer that touches CAMDS is swapped, so both backends
answer the same eighteen calls. Set `CAMDS_USE_API=0` to drive the browser DOM
instead.

The API is addressed through the signed-in browser context, because
`POST /api/login` takes a page-encrypted username and password plus a CAPTCHA:
sign-in stays manual, everything after it is JSON. It is not only faster, it
avoids three things the DOM path cannot:

- the ISO 1043 symbol wizard, since `createInitMaterial` takes the
  classification as a parameter;
- name-based substance lookup, since `findSubstanceByCondition` answers with a
  catalogue id;
- walking a rendered tree to verify, since `loadMdsTree` returns the whole saved
  tree in one call.

Every call sends `Origin` and `Referer`. Without them CAMDS answers a recorded
path with **HTTP 404**, which reads as a missing endpoint rather than a rejected
caller; with them, reads succeed on a signed-in session.

Before an import starts, the backend proves the session with one read-only call.
That happens before the journal exists and before CAMDS allocates any id, so a
lapsed session costs nothing: the run stops with no draft to reconcile. **Test
API session** in the CAMDS tab runs the same check on demand.

Endpoints, fields and their evidence are in
[CREATE_COMPONENT_API.md](camds/CREATE_COMPONENT_API.md). Mass, quantity and
every portion live on the parent relation (`structureVO`), not on the node, so
they travel with it on every write.

The importer creates new Materials first, saves the root, adds Substances by
exact CAS with Fixed/From-To/Rest proportions and saves after each change. It
then opens the Material through Search/View to verify the saved composition.
Next it creates and saves the parent Component, recursively adds Components
with quantities and measured masses, attaches exact Material ID/version
references with mass in grams, and saves after each step. Finally it searches
the saved parent ID and verifies the tree, reference IDs, quantities and masses.
Success is reported only after this read-back completes.

Supported: Component hierarchies, new Materials in any classification recorded
from the CAMDS creation wizard (`camds/material_classifications.json`, 54 codes
including letter-suffixed ones such as `5.1.a`) with basic Substances, and
explicit existing Material references. Material root imports are supported too. A Component's child
Component mass is treated as per-item mass and multiplied by quantity; Material
mass is the mass of its reference in the parent. Preflight requires totals to
match within 0.1%; missing values are not guessed.

Material classifications come from `camds/material_classifications.json`, which
is generated from a recorded run of the creation wizard: its first column holds
the bare code, so a report's `7.2: Ceramics / glass` is reduced to `7.2` before
the cell is clicked. A code that was never recorded still fails closed. Only
`1.1.1` has been created end to end against CAMDS; the rest are observed as
selectable but not yet exercised.

Node type, name, part/material number, mass, quantity, percentage/min/max/Rest,
CAS, substance name, Material classification and remark are mapped. Marking
statements and ELV exemption prose share the report's rightmost column with real
applications but are different IMDS fields; they are kept as `column_note` and
reported as not transferred instead of blocking the import. Fields that would be
written but have no discovered CAMDS control are still rejected at preflight
rather than silently dropped.

A Semicomponent is inserted with the recorded `img[title="Add SemiComponent"]`,
which adds and selects the node directly with no reference dialog. It carries an
Article Name, a Semicomponent No. and a Mass in grams, and CAMDS shows no
Quantity for it, so its mass counts once in the parent total.

The input CAMDS asks for depends on the parent, so the importer picks it by
parent type: a Material under a **Component** carries a Mass in grams, while the
same Material under a **Semicomponent** carries a Proportion. That Proportion is
the widget the Substance path already uses - radio values 1 From-To, 2 Fixed,
3 Rest - so both share one implementation. A Semicomponent nested inside another
Semicomponent is still refused: the toolbar offers it but the insertion was never
exercised. See
[SEMICOMPONENT_APPLICATION_DISCOVERY.md](camds/SEMICOMPONENT_APPLICATION_DISCOVERY.md).

### Applications

An application is a regulatory statement, and CAMDS option codes are **not**
IMDS codes: in this project's own report IMDS `63` on Glass reads "listed under
10(b), 10(c) and 10(d)", while CAMDS `63` on Lead reads "8(g)(ii-ii): single die
300 mm2 or larger (EU)" - the same number, a different legal claim. The parsed
id is therefore never copied across. Only wording is compared.

Applications live on a Material tab with one row per substance, shown
conditionally. For each such substance the importer opens that row's dialog and
reads the options CAMDS offers *there* - they depend on the substance and change,
so no catalogue is stored. Resolution order:

1. A pairing already approved in `config/application_mapping.json` is used.
2. Otherwise an option whose normalised wording equals the report's is accepted.
   Normalisation folds Unicode (the micro sign against mu is the only difference
   in the nickel release-rate option) and drops the trailing IMDS `[33]`. Two
   matches are not a tie to break: they mean the wording identifies nothing.
3. Anything else is **left unset**, recorded, and listed when the run finishes.
   The import continues: an unset application is visibly missing in CAMDS, while
   a wrong one is a false regulatory statement that looks correct.

Every resolution is written back to `config/application_mapping.json` with its
wording, its source and a timestamp, so the next run is deterministic. A stored
pairing is re-checked against the live wording before use: if CAMDS now shows
different text for that value, or no longer offers it, that application is
skipped instead of confirming something nobody approved. A Material-level application is still
refused - only the per-substance rows have been observed.

The Confirm button in that dialog had never been exercised during discovery, so
this write path is implemented from the recorded UI but its persistence is not
yet proven against CAMDS.

Not yet supported, because the corresponding CAMDS controls have not been
observed in a discovery session and this project does not guess selectors:
Semicomponent nested inside a Semicomponent, nested Material creation,
Material-level applications, GADSL/SVHC entry, recyclate entry, Module creation,
editing an already saved MDS, uploads, and Send/Submit.

Repeated names import normally. A board really does carry 30 parts named
"Resistor", and a child may share its parent name, so tree nodes are addressed
by document order - the order the importer added them - and every value is
checked again in that order during read-back.

Not every Substance has a CAS. System groups such as "Misc., not to declare"
never do, so a Substance without a real CAS is looked up by name and the lookup
refuses anything but a single exact match. The IMDS "system" placeholder is not
treated as a CAS: two system groups with different names stay different
substances. Repeated substances of one Material are combined before import by
adding their portions - Fixed values sum, ranges sum bound by bound, and Rest
absorbs, because the remainder is still the remainder.

IMDS prints "Rest 7.98": Rest is the portion type and the number is the value it
resolves to, not a second Fixed percentage. A PDF line break inside a hyphenated
word leaves "lead- based"; the hyphen is rejoined, which repairs both chemical
names and application wording. Save verification is not a regulatory Check/Release pass.

Each run writes `output/camds_imports/<fingerprint>.jsonl` with allocated IDs and
steps. If interrupted, already saved objects remain. Replay of the same parsed
tree/mapping is blocked by default to avoid duplicate IDs. The browser is
retained for inspection when an operation fails.

### Progress, Pause and Stop

The review dialog's plan is also the progress denominator, so the Progress tab
shows a real `12 / 148` counter plus the current node's type, parent path, the
field being filled, succeeded/failed tallies, elapsed time and an ETA. Failures
are logged per node. **Pause** and **Stop** take effect only *between* whole
CAMDS steps — never inside a Create or Save — so a stopped run always leaves a
journal that matches what CAMDS actually holds. Stopping does not roll anything
back.

### Resume

Ticking **Resume** in the review dialog continues a previous run of the exact
same tree and mapping: Materials that reached read-back verification are skipped
and their allocated IDs reused, so no duplicates are created. Resume is refused,
with the allocated IDs named in the message, when the previous run left a
half-built draft — a Material created but never verified, or the parent
Component already created. Re-entering a saved draft editor needs an
"open saved MDS for editing" flow that has not been discovered against CAMDS;
until then those cases are finished manually in CAMDS. A completed import is
never resumed.

See [the live workflow evidence](camds/SAVE_TEST_55095125.md). New application
code is tested with local browser fixtures and simulated transfer scenarios;
the updated app has not run an additional production import automatically.

### What has actually been written to CAMDS

Only Material classification `1.1.1`, Component and Substance-by-CAS have been
created and read back against production, and that was through the browser. The
API backend is derived from seven recorded sessions and **no API write has been
executed from this application**. Neither has:

- the other 53 Material classifications,
- Semicomponent insertion and the Semicomponent-to-Material portion,
- writing an application.

A first live run therefore exercises unproven paths. Start with the smallest
branch that covers the fewest of them, check the result in CAMDS, and widen from
there.

## CAMDS Search / Create

Open the **CAMDS Search / Create** tab (also available from the CAMDS menu), then
click **Open CAMDS browser**. This is the application's own Chromium session,
separate from the Codex in-app browser. It reuses a saved Test Login session if
available; otherwise sign in and complete the slider manually in the new window.
Use CAMDS in English.

### One session for everything

Sign-in, Search, Create and tree import all run on this single page. **Sign in to
CAMDS** (or `CAMDS -> Login CAMDS`) authenticates that same browser rather than a
second throwaway one, so the app can never show "Logged in" while the browser
that performs the next operation is anonymous. While the session is idle the app
re-reads the live page every 20 seconds; if the session lapses, the connection
indicator, status, stage and state machine all move to "expired" together and
operations stop until you sign in again. `Test Login` in Settings remains a
separate isolated check and performs no MDS operations.

- Search Component, Semicomponent, Material, Basic Substance or All MDSs by name,
  CAMDS ID, part/material number, or CAS as applicable. Choose Own, Published,
  Accepted or All sources. At least one criterion is required. The current result
  page is displayed in the app; fixed-column table clones are not duplicated.
- Prepare a Component, Semicomponent or Material root using the form. You can
  select a parsed IMDS node and click **Use selected IMDS node** to preload its
  name, number, mass and classification for review. Component mass is in grams.
- **Create and fill root** opens a new MDS, fills the supported
  root fields and reads those fields back. CAMDS allocates a displayed ID when
  the form opens. The browser remains open for review; **Save open draft** issues
  Save separately and retains the editor. Manual Save does not claim persistence
  verification; full tree import verifies through Search/View. Only Material classification `1.1.1` has
  been validated for this flow; unsupported classifications fail before Create.
- While an editor is open (including after a partial failure), Search, Create and
  tree import are disabled to prevent losing the form or allocating duplicate
  IDs. Only **Save open draft** and **Leave editor** are accepted. **Leave
  editor** asks for confirmation, then navigates back to Search using the same
  step the tree import performs between Materials; if CAMDS raises an
  unsaved-data dialog it is never accepted automatically and the editor is left
  untouched. Unsaved form data is discarded, but a draft already saved keeps its
  allocated ID. Leaving stays available after a failure, so recovering does not
  require discarding the whole browser session. **Close browser session** also
  finishes, without preserving unsaved data.

The `CREATE_ROOT` workflow mode opens the manual root form. `DRY_RUN` remains a
local plan only. `IMPORT_TREE` opens the full transfer review. Delete, Send,
Submit, Propose, attachment upload and Module creation are not automated.
No generic "Confirm" handler is used: classification-wizard Next is distinct
from the editor's Next/Save navigation.

## Setup and launch

**Python 3.11 or newer** - `StrEnum` is used throughout, and 3.10 fails at
import. Install Python and Git first on a machine that does not have them.

```powershell
git clone https://github.com/hanhle97/camds-transfer.git
cd camds-transfer
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r camds_imds_importer\requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m camds_imds_importer.app
```

`playwright install chromium` downloads its own browser, a few hundred MB. A
Chrome or Edge already on the machine is not used.

The window and taskbar icon is CAMDS's own mark, taken from
`catarc.camds.org.cn/favicon.ico` and shipped in `ui/assets/camds.ico`. It
belongs to CATARC, not to this project; it is here so the window is easy to
pick out, and it does not make this an official CAMDS tool. 32x32 is the only
size CAMDS publishes, so Windows scales it up for the larger taskbar slots.

### What travels with the repository, and what does not

| | Where | Travels |
|---|---|---|
| Application and substance choices | `config/*.json` | **yes**, tracked |
| CAMDS sign-in | `.runtime/camds_storage_state.json` | no - sign in again, and the CAPTCHA means by hand |
| Import journals | `output/camds_imports/*.jsonl` | no |
| Supplier IMDS PDFs | anywhere | no - customer data, and an input rather than source |

The journal is what makes **Resume** work, so a run interrupted on one machine
cannot be resumed on another unless that `.jsonl` is copied across by hand. Its
filename is a fingerprint of the parsed tree, so it has to keep the name it has.

Nothing in `config/` is a credential, and nothing has to be filled in before
the first launch: the app parses a PDF and runs its whole preflight with no
CAMDS session, and asks for one only when an operation touches CAMDS.

The parser remains available as a CLI:

```powershell
.\.venv\Scripts\python.exe -m camds_imds_importer.app parse path\to\report.pdf
```

Run ordinary tests without the real login integration test:

```powershell
$env:QT_QPA_PLATFORM="offscreen"
.\.venv\Scripts\python.exe -m pytest -q -m "not integration"
```

## Slow links to CAMDS

CAMDS is reachable from outside China but with a high round-trip time, and the
site is a large single-page application. Navigation therefore waits for the
response to commit and then for the application shell, instead of waiting for
every synchronous script as `domcontentloaded` does; the budget is 180 seconds
and can be changed with `CAMDS_NAVIGATION_TIMEOUT_MS`. If the first load still
does not finish, the browser is **kept open** with a notice rather than
discarded — let the page finish, sign in and select English there, then run the
operation again. Each operation re-checks that the page is ready before acting,
so a slow start cannot cause a half-applied change.

## Credentials

Credential lookup order is environment variables, Windows Credential Manager through `keyring`, then values entered in the CAMDS Account dialog for the current session. Passwords are never stored in YAML, JSON, source code, logs, screenshots, or tests. Browser storage state is kept below `.runtime/`, which is ignored by Git.

## Architecture

- `parser/`: native PDF extraction, row parsing, canonical models, and tree construction.
- `validation/`: canonical-data checks independent of CAMDS.
- `core/`: state machine, progress, events, checkpointing, configuration, credentials, and sanitized logging.
- `workers/`: QObject workers executed on dedicated QThreads.
- `ui/`: modular PySide6 tabs and dialogs.
- `camds/`: async Playwright browser, scoped Search/Create operations, selector strategies, verification detection, session state, and discovery snapshots.

The dependency direction remains PDF -> canonical JSON -> validation -> CAMDS mapping -> browser automation. CAMDS modules never read PDFs.

After a successful Test Login, use `CAMDS -> Discover Authenticated Page` to open a read-only discovery session for two minutes. Navigate through CAMDS screens manually in the headed browser; a snapshot is captured whenever the URL/route changes under `debug/authenticated-home/<route>/`. Each snapshot records the URL, title, DOM, visible controls, and screenshot so selectors can be reviewed before any data-entry implementation.

**Record classification wizard** (CAMDS Search / Create tab) captures the
"Creation of a new material" dialog, which the route-based discovery loop cannot
see because a modal does not change the URL. CAMDS allocates the MDS ID only when
Next opens the editor, so this action stops at the dialog, records its rows,
tree nesting depth and buttons, then closes it — Next is never pressed and no MDS
is created. The snapshot lands in `debug/material-classifications/`. If the
dialog cannot be closed the session is locked rather than treated as idle. This
is the evidence needed before Material classifications other than `1.1.1` can be
created automatically.

The action policy explicitly classifies `DELETE`, `SEND`, `PROPOSE`, and `SUBMIT` as sensitive. These actions raise an exception unless an explicit confirmation is supplied by the interactive UI. No destructive or submission operation is currently wired to a browser button.

## Parser assumptions

- The report is the standard IMDS landscape table with native text and stable semantic columns.
- Hierarchy markers contain a numeric tree level at the left edge (`1` or `|- N`).
- CAS values follow `NN...-NN-N`; the IMDS `system` placeholder is preserved explicitly.
- Node type is inferred after tree construction from CAS/system identifiers, classification, quantity, part number, and child types—not from level alone.
- Every node keeps its source page and normalized raw row text.

## Known edge cases

- Wrapped names, classifications, application text, and ranges are reconstructed within their table row.
- Single percentages, min-max percentages, and `Rest` are distinct canonical values.
- Missing values remain `null`; malformed hierarchy jumps generate warnings.
- Scanned/image-only reports require a separate OCR path and are not silently accepted.

## Safe Test Login

Test Login launches headed Chromium, fills credentials through DOM locators, and performs no MDS operations. CAPTCHA, slider, MFA, and security confirmation remain manual. The integration test runs only when both credential environment variables exist and is selected explicitly with `-m integration`.

When verification is detected, the desktop app shows a verification dialog with the latest browser image and an optional CAPTCHA-code field. A user-entered code is passed to generic CAPTCHA input selectors when available; the tool never reads, derives, or solves the code. Slider verification is completed by the user in the headed browser.

`DRY_RUN` recursively writes `output/<document>/dry_run_plan.json` with intended searches and field values, without modifying CAMDS. `IMPORT_TREE` transfers the reviewed tree as drafts. Submit remains unavailable.

Live discovery and draft workflow verification are documented in [AUTHENTICATED_DISCOVERY.md](camds/AUTHENTICATED_DISCOVERY.md), [CHILD_NODE_DISCOVERY.md](camds/CHILD_NODE_DISCOVERY.md) and [SAVE_TEST_55095125.md](camds/SAVE_TEST_55095125.md). The selector inventories distinguish observations from verification. Module editor variants and unsupported classifications remain unverified. Opening a Create form immediately displays a new ID, so read-only discovery must not open Create forms indiscriminately. Prefer exact roles and scoped form labels; avoid absolute XPath, coordinates, and ambiguous global input selectors.

Automated Search/Create tests use local HTML fixtures and do not send requests
to production CAMDS. Production selector discovery is evidence for implementation,
not a live end-to-end test of the new application flow.
