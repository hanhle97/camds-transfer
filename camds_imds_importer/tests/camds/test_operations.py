from __future__ import annotations

import json
import pytest
from playwright.async_api import async_playwright

from camds_imds_importer.camds.operations import CamdsOperations, CreateRequest, SearchRequest, form_item


@pytest.fixture
async def page():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        page.set_default_timeout(1000)
        yield page
        await browser.close()


@pytest.mark.parametrize("operation_request", [
    SearchRequest("Component"), SearchRequest("Basic Substance", identifier="123"),
    SearchRequest("Material", cas="7440-02-0"), SearchRequest("Unknown", name="x"),
    CreateRequest("Component", "x", weight_g="nan"),
    CreateRequest("Component", "x", weight_g="-1"),
    CreateRequest("Component", "x", weight_g=""),
    CreateRequest("Material", "x", classification="8.4"),
    CreateRequest("Substance", "x"), CreateRequest("Semicomponent", ""),
    CreateRequest("Semicomponent", "x", number="x" * 51),
])
def test_invalid_requests_fail_before_browser_access(operation_request):
    with pytest.raises(ValueError):
        operation_request.validate()


def item(label, control="<input>"):
    return f'<div class="el-form-item"><label class="el-form-item__label">{label}</label>{control}</div>'


async def test_label_locator_excludes_nested_parent_and_background(page):
    await page.set_content(item("Outer", item("Material Name:", "<input value='background'>")) +
        '<div role="dialog" aria-label="Detail">' + item("Material Name:", "<input value='dialog'>") + '</div>')
    dialog = page.get_by_role("dialog", name="Detail")
    assert await form_item(dialog, "Material Name:").count() == 1
    assert await form_item(dialog, "Material Name:").locator("input").input_value() == "dialog"
    assert await form_item(page, "Material Name:").count() == 2


async def test_result_reader_uses_complete_table_without_fixed_column_duplicates(page):
    await page.set_content('''
      <table class="el-table__header"><tr><th>No.</th><th>Name</th><th>ID/Version</th><th>Actions</th><th></th></tr></table>
      <table class="el-table__body"><tbody><tr><td>1</td><td>A</td><td>CA_1 / 1</td><td>View</td><td></td></tr></tbody></table>
      <table class="el-table__header"><tr><th>No.</th><th>Actions</th></tr></table>
      <table class="el-table__body"><tbody><tr><td>1</td><td>View</td></tr></tbody></table>
    ''')
    ops = CamdsOperations(page)
    ops.results_ready = True
    result = await ops.read_results()
    assert result["columns"] == ["No.", "Name", "ID/Version"]
    assert result["rows"] == [["1", "A", "CA_1 / 1"]]


async def test_search_by_cas_submits_only_search_and_reads_results(page, monkeypatch):
    content = '<button role="tab" aria-selected="false">Basic Substance</button>'
    content += item("Name / Synonym / English Name:") + item("CAS No.:")
    content += '<button id="search">Search</button><div id="results"></div>'
    content += '''<script>
      document.querySelector('[role=tab]').onclick = e => e.target.setAttribute('aria-selected','true');
      document.querySelector('#search').onclick = () => {
        window.submitted = [...document.querySelectorAll('input')].map(x=>x.value);
        document.querySelector('#results').innerHTML = '<button>newSearch</button><table class="el-table__header"><tr><th>CAS No.</th><th>Name</th></tr></table><table class="el-table__body"><tbody><tr><td>7440-02-0</td><td>Nickel</td></tr></tbody></table>';
      };
    </script>'''
    ops = CamdsOperations(page)
    async def navigate(_):
        await page.set_content(content)
    monkeypatch.setattr(ops, "_navigate", navigate)
    result = await ops.search(SearchRequest("Basic Substance", cas="7440-02-0"))
    assert await page.evaluate("window.submitted") == ["", "7440-02-0"]
    assert result["rows"] == [["7440-02-0", "Nickel"]]


@pytest.mark.parametrize("kind,number_label,name_label", [
    ("Component", "Component No.", "Article Name"),
    ("Semicomponent", "Semicomponent No.", "Article Name"),
    ("Material", "Material No.", "Material Name"),
])
async def test_create_fills_root_without_save_and_blocks_second_operation(page, monkeypatch, kind, number_label, name_label):
    ops = CamdsOperations(page)
    row_name = {"Component": "Component (including complete vehicles)", "Semicomponent": "Semi- Components", "Material": "Materials"}[kind]
    editor = '<div role="tabpanel" aria-label="Details">'
    editor += item("Type", kind) + item("ID / Version", "CA_TEST / 0.01")
    editor += item(name_label) + item(number_label) + item("Remark", "<textarea></textarea>")
    if kind == "Component":
        editor += item("Measured Mass per Item", '<input><button>g</button>')
    editor += '</div><button onclick="window.saved=true">Save</button>'
    wizard = '<div role="dialog" aria-label="Creation of a new material"><table><tr><td>1.1.1</td></tr></table><button id="next">Next</button></div>'
    html = f'<table><tr><td>{row_name}</td><td><button id="create">Create</button></td></tr></table><div id="editor"></div>'
    html += '<script>window.saved=false;const editor=' + json.dumps(editor) + ';const wizard=' + json.dumps(wizard) + ';'
    html += "document.querySelector('#create').onclick=()=>{"
    if kind == "Material":
        html += "document.querySelector('#editor').innerHTML=wizard;document.querySelector('#next').onclick=()=>document.querySelector('#editor').innerHTML=editor;"
    else:
        html += "document.querySelector('#editor').innerHTML=editor;"
    html += '};</script>'
    async def navigate(_):
        await page.set_content(html)
    monkeypatch.setattr(ops, "_navigate", navigate)
    result = await ops.create(CreateRequest(kind, "Test root", "P123", "12.5" if kind == "Component" else "", "1.1.1" if kind == "Material" else "", "Test remark"))
    assert await form_item(page, name_label).locator("input").input_value() == "Test root"
    assert await form_item(page, number_label).locator("input").input_value() == "P123"
    assert await page.evaluate("window.saved") is False
    assert "NOT saved" in result["note"]
    assert ops.editor_open
    # Use the real navigation guard; it must fail before any page navigation.
    with pytest.raises(RuntimeError, match="editor is open"):
        await CamdsOperations._navigate(ops, "https://example.invalid/")


async def test_invalid_create_does_not_allocate_id(page):
    await page.set_content('<button onclick="window.allocated=true">Create</button>')
    ops = CamdsOperations(page)
    with pytest.raises(ValueError):
        await ops.create(CreateRequest("Material", "Bad", classification="8.4"))
    assert not ops.editor_open
    assert await page.evaluate("Boolean(window.allocated)") is False


async def test_save_waits_for_loading_but_does_not_claim_persistence(page):
    await page.set_content('''<button id="save">Save</button><div id="status"></div>
    <script>document.querySelector('#save').onclick=()=>{
      document.querySelector('#status').innerHTML='<div class="el-loading-mask">loading</div>';
      setTimeout(()=>document.querySelector('#status').innerHTML='<button>Check</button>', 150);
    };</script>''')
    ops = CamdsOperations(page)
    ops.editor_open = True
    result = await ops.save()
    assert ops.editor_open
    assert result["editor_open"] is True
    assert "persistence is verified" in result["note"]


async def test_save_rejects_validation_errors_even_with_existing_check_button(page):
    await page.set_content('''<button id="save">Save</button><button>Check</button><div id="errors"></div>
    <script>document.querySelector('#save').onclick=()=>document.querySelector('#errors').innerHTML=
    '<div class="el-form-item__error">Quantity is required</div>';</script>''')
    ops = CamdsOperations(page)
    ops.editor_open = True
    with pytest.raises(RuntimeError, match="Quantity is required"):
        await ops.save()
    assert ops.editor_open


async def test_tree_selector_scopes_repeated_material_names_to_parent(page):
    from camds_imds_importer.camds.tree_import import DraftBrowser
    await page.set_content('''<ul><li treenode><a><span class="node_name">Root</span></a><ul>
      <li treenode><a><span class="node_name">A</span></a><ul><li treenode><a><span class="node_name">Steel</span></a></li></ul></li>
      <li treenode><a><span class="node_name">B</span></a><ul><li treenode><a><span class="node_name">Steel</span></a></li></ul></li>
    </ul></li></ul>''')
    backend = DraftBrowser(CamdsOperations(page))
    node = await backend.tree_node(["Root", "B", "Steel"])
    assert await node.count() == 1
    await backend.verify_child_count(["Root"], 2)
