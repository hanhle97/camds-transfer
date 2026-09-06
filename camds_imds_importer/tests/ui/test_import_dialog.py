from types import SimpleNamespace
import threading

from PySide6.QtWidgets import QApplication

from camds_imds_importer.ui.import_dialog import ImportDialog
from camds_imds_importer.ui.camds_tab import CamdsTab
from camds_imds_importer.workers.operations_worker import OperationsWorker


def test_import_dialog_requires_revalidation_after_mapping_edit():
    app = QApplication.instance() or QApplication([])
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 1, "children": [
        {"uid": "m", "node_type": "MATERIAL", "name": "Polymer", "weight_g": 1, "classification": "8.4", "children": []}]}
    dialog = ImportDialog(root)
    assert not dialog.start.isEnabled()
    dialog.mapping_table.item(0, 3).setText("CA_8_123")
    dialog.mapping_table.item(0, 4).setText("1")
    dialog.validate_plan()
    assert dialog.start.isEnabled()
    dialog.mapping_table.item(0, 4).setText("")
    assert not dialog.start.isEnabled()
    dialog.close()


def test_open_session_has_no_undefined_editor_variable(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(OperationsWorker, 'start', lambda self: None)
    tab = CamdsTab()
    tab.open_session()
    assert tab.worker is not None
    assert tab.busy
    tab._finished()
    tab.close()


def test_manual_save_accepts_none_and_keeps_editor_locked():
    app = QApplication.instance() or QApplication([])
    tab = CamdsTab()
    sent = []
    tab.worker = SimpleNamespace(stopping=threading.Event(), submit=lambda *args: sent.append(args))
    tab.editor_open = True
    tab.save()
    assert sent == [("save", None)]
    tab._result({"kind": "save", "editor_open": True, "note": "pending readback"})
    assert tab.editor_open
    assert not tab.forms.isEnabled()
    tab.worker = None
    tab.close()


def sound_tree(**material):
    substance = {"uid": "s", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6",
                 "percentage": 100, "children": []}
    mat = dict({"uid": "m", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1",
                "weight_g": 1.0, "children": [substance]}, **material)
    return {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 1.0, "children": [mat]}


def test_findings_are_shown_but_do_not_block_the_transfer():
    QApplication.instance() or QApplication([])
    root = sound_tree()
    # Masses that do not add up and a composition below 100% are the supplier's
    # data: they are reported and imported as declared.
    root["children"][0]["weight_g"] = 0.9
    root["children"][0]["children"][0]["percentage"] = 90
    dialog = ImportDialog(root)
    text = dialog.preview.toPlainText()
    assert dialog.start.isEnabled(), "findings must not block a transfer"
    assert "imported as declared" in text
    assert "composition totals 90-90%" in text
    assert "0.9 g against 1 g declared" in text
    dialog.close()


def test_combined_substances_are_listed_before_the_operator_starts():
    QApplication.instance() or QApplication([])
    root = sound_tree(children=[
        {"uid": "a", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6",
         "percentage": 40.0, "children": []},
        {"uid": "b", "node_type": "SUBSTANCE", "name": "Iron", "cas_number": "7439-89-6",
         "percentage": 60.0, "children": []}])
    dialog = ImportDialog(root)
    assert "Combined 1 repeated substance entries" in dialog.preview.toPlainText()
    assert "merged 2 entries of Iron" in dialog.preview.toPlainText()
    assert dialog.start.isEnabled()
    dialog.close()


def test_a_blocked_root_does_not_prevent_importing_a_good_branch():
    QApplication.instance() or QApplication([])
    good = {"uid": "g", "node_type": "COMPONENT", "name": "Cap", "weight_g": 1.0, "quantity": 1,
            "children": [{"uid": "gm", "node_type": "MATERIAL", "name": "Steel", "classification": "1.1.1",
                          "weight_g": 1.0, "children": [
                              {"uid": "gs", "node_type": "SUBSTANCE", "name": "Iron",
                               "cas_number": "7439-89-6", "percentage": 100, "children": []}]}]}
    # The root carries a child type the importer cannot write, so it is blocked.
    blocked = {"uid": "b", "node_type": "COMPONENT", "name": "Bad", "weight_g": 1.0, "quantity": 1,
               "children": [{"uid": "bm", "node_type": "MODULE", "name": "Module", "children": []}]}
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Whole report", "weight_g": 2.0,
            "children": [good, blocked]}

    dialog = ImportDialog(root)
    assert not dialog.start.isEnabled(), "the whole report cannot be transferred"
    assert "MODULE" in dialog.preview.toPlainText()

    index = next(i for i in range(dialog.subtree.count()) if dialog.subtree.itemData(i)["uid"] == "g")
    dialog.subtree.setCurrentIndex(index)
    assert dialog.start.isEnabled(), "the good branch must be transferable on its own"
    assert dialog.request.root["uid"] == "g"
    assert [m["uid"] for m in dialog.materials] == ["gm"]
    dialog.close()


def test_switching_branch_rebuilds_the_material_mapping():
    QApplication.instance() or QApplication([])
    def branch(uid, material_name):
        return {"uid": uid, "node_type": "COMPONENT", "name": "Branch " + uid, "weight_g": 1.0, "quantity": 1,
                "children": [{"uid": uid + "m", "node_type": "MATERIAL", "name": material_name,
                              "classification": "1.1.1", "weight_g": 1.0, "children": [
                                  {"uid": uid + "s", "node_type": "SUBSTANCE", "name": "Iron",
                                   "cas_number": "7439-89-6", "percentage": 100, "children": []}]}]}
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Root", "weight_g": 2.0,
            "children": [branch("a", "Steel"), branch("b", "Copper")]}
    dialog = ImportDialog(root)
    index = next(i for i in range(dialog.subtree.count()) if dialog.subtree.itemData(i)["uid"] == "b")
    dialog.subtree.setCurrentIndex(index)
    assert dialog.mapping_table.rowCount() == 1
    assert dialog.mapping_table.item(0, 0).text() == "Copper"
    # A reference typed for one branch must not leak into another.
    dialog.mapping_table.item(0, 3).setText("CA_8_1")
    assert not dialog.start.isEnabled()
    index = next(i for i in range(dialog.subtree.count()) if dialog.subtree.itemData(i)["uid"] == "a")
    dialog.subtree.setCurrentIndex(index)
    assert dialog.mapping_table.item(0, 0).text() == "Steel"
    assert dialog.mapping_table.item(0, 3).text() == ""
    dialog.close()
