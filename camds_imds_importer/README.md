# CAMDS IMDS Importer

A Windows desktop application that converts native-text IMDS MDS Report PDFs into a canonical hierarchy, validates the result, and provides a safe CAMDS authentication boundary. This iteration never creates, edits, saves, or submits CAMDS MDS data.

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
- `camds/`: async Playwright browser, selector strategies, verification detection, session state, and discovery snapshots.

The dependency direction remains PDF -> canonical JSON -> validation -> CAMDS mapping -> browser automation. CAMDS modules never read PDFs.

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

The CAMDS URL returned the expected page title during development, but its body did not finish rendering from this environment. Live selector confirmation and authenticated URL verification therefore remain pending. Selector strategies prefer roles, labels, placeholders, stable names, and CSS fallbacks; absolute XPath and coordinate automation are not used.
