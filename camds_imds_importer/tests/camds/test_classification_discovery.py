"""Recording the classification wizard must never allocate a CAMDS ID."""
import json

import pytest
from playwright.async_api import async_playwright

from camds_imds_importer.camds.discovery import capture_dialog, discover_material_classifications
from camds_imds_importer.camds.operations import CamdsOperations

# CAMDS allocates the MDS ID when Next opens the editor. This page therefore
# only reveals an ID if Next is pressed, so the test fails loudly if it ever is.
CREATE_PAGE = """
<table><tr><td role="cell">Materials</td><td><button onclick="openDialog()">Create</button></td></tr>
       <tr><td role="cell">Component (including complete vehicles)</td><td><button>Create</button></td></tr></table>
<div id="wiz" role="dialog" aria-label="Creation of a new material" style="display:none">
  <button class="el-dialog__headerbtn" onclick="closeDialog()">x</button>
  <table>
    <tr><td>1.1.1: Steel</td></tr>
    <tr><td>1.1.2: highly alloyed</td></tr>
    <tr><td>5.3: Elastomers / elastomeric compounds</td></tr>
    <tr><td>6.1: Lacquers</td></tr>
    <tr><td>Not a code</td></tr>
  </table>
  <button onclick="pressNext()">Next</button>
  <button onclick="closeDialog()">Cancel</button>
</div>
<div id="allocated"></div>
<script>
  function openDialog() { document.getElementById('wiz').style.display = 'block'; }
  function closeDialog() { document.getElementById('wiz').style.display = 'none'; }
  function pressNext() { document.getElementById('allocated').innerText = 'CA_8_999 / 0.01'; closeDialog(); }
</script>
"""


@pytest.fixture
async def page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.set_default_timeout(3000)
        yield page
        await browser.close()


async def operations_on(page, body):
    await page.set_content(body)
    ops = CamdsOperations(page)
    # The create-page readiness check navigates; the fixture is already loaded.
    ops._navigate = lambda url: _noop()
    return ops


async def _noop():
    return None


async def test_wizard_is_recorded_and_closed_without_allocating_an_id(page, tmp_path):
    ops = await operations_on(page, CREATE_PAGE)
    result = await discover_material_classifications(ops, tmp_path)
    assert "1.1.2" in result["codes"] and "5.3" in result["codes"] and "6.1" in result["codes"]
    assert "Not a code" not in result["codes"]
    assert await page.locator("#allocated").inner_text() == "", "Next was pressed; that allocates a CAMDS ID"
    assert not await page.get_by_role("dialog", name="Creation of a new material").is_visible()
    assert not ops.editor_open
    written = json.loads((tmp_path / "dialog_snapshot.json").read_text(encoding="utf-8"))
    assert written["codes"] == result["codes"]
    assert (tmp_path / "dialog.html").exists() and (tmp_path / "dialog.png").exists()
    assert any(b["text"] == "Next" for b in result["buttons"]), "the Next control is recorded, not used"


async def test_a_wizard_that_will_not_close_locks_the_session(page, tmp_path):
    ops = await operations_on(page, CREATE_PAGE.replace("onclick=\"closeDialog()\"", ""))
    with pytest.raises(RuntimeError, match="did not close"):
        await discover_material_classifications(ops, tmp_path)
    # Fail closed: a stuck modal must not look like an idle session.
    assert ops.editor_open
    assert await page.locator("#allocated").inner_text() == ""


async def test_missing_wizard_fails_instead_of_guessing(page, tmp_path):
    ops = await operations_on(page, "<table><tr><td role='cell'>Materials</td><td><button>Create</button></td></tr></table>")
    with pytest.raises(Exception):
        await discover_material_classifications(ops, tmp_path)
    assert not (tmp_path / "dialog_snapshot.json").exists()


async def test_capture_records_tree_depth_so_multi_level_pickers_are_visible(page, tmp_path):
    await page.set_content("""
      <div role="dialog" aria-label="Creation of a new material">
        <ul><li class="el-tree-node"><span class="el-tree-node__label">5: Polymer materials</span>
          <ul><li class="el-tree-node"><span class="el-tree-node__label">5.3: Elastomers</span></li></ul>
        </li></ul>
      </div>""")
    dialog = page.get_by_role("dialog", name="Creation of a new material")
    result = await capture_dialog(page, dialog, tmp_path)
    labels = {e["text"]: e["depth"] for e in result["entries"] if e["cls"] == "el-tree-node__label"}
    assert labels["5.3: Elastomers"] > labels["5: Polymer materials"], "nesting depth must be recorded"
    assert result["codes"] == ["5", "5.3"]


# A polymer classification inserts a second wizard step before the editor.
SYMBOL_STEP = """
<table><tr><td role="cell">Materials</td><td><button onclick="openDialog()">Create</button></td></tr></table>
<div id="wiz" role="dialog" aria-label="Creation of a new material" style="display:none">
  <table><tr><td>5.4.3</td></tr></table>
  <button onclick="toSymbols()">Next</button>
</div>
<div id="sym" role="dialog" aria-label="Creation of a new material" style="display:none">
  <p>Basic polymers ISO 1043-1 or GB/T 1844.1:</p>
  <p>Composed symbol:</p><input>
  <button>Cancel</button><button>Next</button>
</div>
<div id="allocated"></div>
<script>
  function openDialog() { document.getElementById('wiz').style.display = 'block'; }
  function toSymbols() {
    document.getElementById('wiz').style.display = 'none';
    document.getElementById('sym').style.display = 'block';
    document.getElementById('allocated').innerText = 'CA_8_777 / 0.01';
  }
</script>
"""


async def test_the_iso_1043_symbol_step_stops_the_run_instead_of_timing_out(page, tmp_path):
    from camds_imds_importer.camds.operations import CamdsOperations, CreateRequest
    await page.set_content(SYMBOL_STEP)
    ops = CamdsOperations(page)
    ops._navigate = lambda url: _noop()
    with pytest.raises(RuntimeError, match="ISO 1043 symbols"):
        await ops.create(CreateRequest("Material", "Other duromer", classification="5.4.3"))
    # An ID is already allocated at this point, so the session must stay locked.
    assert ops.editor_open
    assert "Composed symbol" not in (await page.locator("#allocated").inner_text())


def test_long_playwright_snapshots_are_shortened_for_the_ui():
    from camds_imds_importer.camds.import_control import brief
    flood = "Timeout 15000ms exceeded." + chr(10) + chr(10).join(f"  - cell '{i}'" for i in range(400))
    assert len(brief(flood)) < 700
    assert brief(flood).startswith("Timeout 15000ms exceeded.")
    assert "truncated" in brief(flood) or brief(flood).count(chr(10)) <= 6
    # A short message is passed through untouched.
    assert brief("Substance CAS mismatch") == "Substance CAS mismatch"
