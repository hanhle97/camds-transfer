# CAMDS IMDS Importer

A Windows desktop application that converts native-text IMDS MDS Report PDFs into a canonical hierarchy, validates it, searches CAMDS, and transfers supported trees into saved CAMDS drafts with read-back verification. Send/Submit are not automated.

## Import a parsed tree into CAMDS

1. Parse the IMDS PDF and review the hierarchy, mass and quantity values.
2. Open **CAMDS Search / Create → Open CAMDS browser**. Sign in if needed and
   use English. This is the app's browser, separate from the Codex browser.
3. Click **Import parsed tree…** (or use the `IMPORT_TREE` workflow mode).
4. Review each Material. Leave ID/version blank to create a new Material, or
   enter an existing **CAMDS** ID and exact version. Existing Material composition
   is reused as-is, not rewritten from the parser. IMDS IDs are never substituted.
5. Click **Validate and preview**. Unsupported/missing data blocks the entire
   transfer before any ID allocation. Editing a mapping invalidates the preview.
6. Click **Create + Save tree in CAMDS** to start the reviewed plan.

The importer creates new Materials first, saves the root, adds Substances by
exact CAS with Fixed/From-To/Rest proportions and saves after each change. It
then opens the Material through Search/View to verify the saved composition.
Next it creates and saves the parent Component, recursively adds Components
with quantities and measured masses, attaches exact Material ID/version
references with mass in grams, and saves after each step. Finally it searches
the saved parent ID and verifies the tree, reference IDs, quantities and masses.
Success is reported only after this read-back completes.

Supported: Component hierarchies, new Material classification `1.1.1` with basic
Substances, and explicit existing Material references (including other
classifications). Material root imports are supported too. A Component's child
Component mass is treated as per-item mass and multiplied by quantity; Material
mass is the mass of its reference in the parent. Preflight requires totals to
match within 0.1%; missing values are not guessed.

Not yet supported: Semicomponent tree import, nested Material creation,
new Material classifications other than `1.1.1`, system/joker substances,
application mapping, recyclate entry, Module creation, uploads, and Send/Submit.
Duplicate sibling display names and repeated Component names require manual
disambiguation. Save verification is not a regulatory Check/Release pass.

Each run writes `output/camds_imports/<fingerprint>.jsonl` with allocated IDs and
steps. If interrupted, already saved objects remain. Automatic replay of the
same parsed tree/mapping is blocked to avoid duplicate IDs. Inspect the journal
and CAMDS records before deciding how to recover; automatic resume/rollback is
not implemented. The browser is retained for inspection when an operation fails.

See [the live workflow evidence](camds/SAVE_TEST_55095125.md). New application
code is tested with local browser fixtures and simulated transfer scenarios;
the updated app has not run an additional production import automatically.

## CAMDS Search / Create

Open the **CAMDS Search / Create** tab (also available from the CAMDS menu), then
click **Open CAMDS browser**. This is the application's own Chromium session,
separate from the Codex in-app browser. It reuses a saved Test Login session if
available; otherwise sign in and complete the slider manually in the new window.
Use CAMDS in English.

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
- While an editor is open (including after a partial failure), further automated
  operations are disabled to prevent losing the form or allocating duplicate
  IDs. Review the browser and use **Close browser session** to finish. Unsaved
  data is not preserved on close. A new session can then be opened.

The `CREATE_ROOT` workflow mode opens the manual root form. `DRY_RUN` remains a
local plan only. `IMPORT_TREE` opens the full transfer review. Delete, Send,
Submit, Propose, attachment upload and Module creation are not automated.
No generic "Confirm" handler is used: classification-wizard Next is distinct
from the editor's Next/Save navigation.

## Setup and launch

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r camds_imds_importer\requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m camds_imds_importer.app
```

The parser remains available as a CLI:

```powershell
.\.venv\Scripts\python.exe -m camds_imds_importer.app parse path\to\report.pdf
```

Run ordinary tests without the real login integration test:

```powershell
$env:QT_QPA_PLATFORM="offscreen"
.\.venv\Scripts\python.exe -m pytest -q -m "not integration"
```

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
