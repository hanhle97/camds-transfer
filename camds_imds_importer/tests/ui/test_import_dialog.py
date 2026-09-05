from types import SimpleNamespace
import threading

from PySide6.QtWidgets import QApplication

from camds_imds_importer.ui.import_dialog import ImportDialog
from camds_imds_importer.ui.camds_tab import CamdsTab
from camds_imds_importer.workers.operations_worker import OperationsWorker


def test_import_dialog_requires_revalidation_after_mapping_edit():
    app = QApplication.instance() or QApplication([])
    root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 1, "children": [
        {"uid": "m", "node_type": "MATERIAL", "name": "Polymer", "weight_g": 1, "classification": "5.1.a", "children": []}]}
    dialog = ImportDialog(root)
    assert not dialog.start.isEnabled()
    dialog.mapping.item(0, 3).setText("CA_8_123")
    dialog.mapping.item(0, 4).setText("1")
    dialog.validate_plan()
    assert dialog.start.isEnabled()
    dialog.mapping.item(0, 4).setText("")
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
