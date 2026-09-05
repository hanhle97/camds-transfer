import threading
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from camds_imds_importer.ui.camds_tab import CamdsTab
from camds_imds_importer.parser.models import MDSNode, NodeType


def test_tab_search_ignores_disabled_criteria_and_serializes_work():
    app = QApplication.instance() or QApplication([])
    tab = CamdsTab()
    submitted = []
    tab.worker = SimpleNamespace(stopping=threading.Event(), submit=lambda *args: submitted.append(args))
    tab._ready()
    tab.search_kind.setCurrentText("Basic Substance")
    tab.search_id.setText("old MDS id")
    tab.search_cas.setText("7440-02-0")
    tab.search()
    tab.search()
    assert len(submitted) == 1
    assert submitted[0][1].identifier == ""
    assert submitted[0][1].cas == "7440-02-0"
    assert not tab.forms.isEnabled()
    tab._result({"kind": "search", "columns": ["CAS"], "rows": [["7440-02-0"]], "note": "one page"})
    assert tab.results.item(0, 0).text() == "7440-02-0"
    assert tab.forms.isEnabled()
    tab.worker = None
    tab.close()


def test_partial_create_failure_locks_controls_but_allows_close():
    app = QApplication.instance() or QApplication([])
    tab = CamdsTab()
    tab.worker = SimpleNamespace(stopping=threading.Event())
    tab._failed("Field missing", True)
    assert not tab.forms.isEnabled()
    assert tab.close_button.isEnabled()
    assert "partially filled" in tab.status.text()
    tab.worker = None
    tab.close()


def test_loading_node_does_not_silently_map_unsupported_classification():
    app = QApplication.instance() or QApplication([])
    tab = CamdsTab()
    node = MDSNode("m", 1, NodeType.MATERIAL, "Polymer", classification="5.1.a", material_number="M1")
    tab.set_document(SimpleNamespace(root=node))
    tab.load_node()
    assert tab.create_classification.currentText() == ""
    assert "unsupported" in tab.status.text()
    assert tab.create_number.text() == "M1"
    tab.close()
