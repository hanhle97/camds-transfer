import threading
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from camds_imds_importer.ui.camds_tab import CamdsTab
from camds_imds_importer.parser.models import MDSNode, NodeType


def test_a_search_asks_about_every_material_in_the_report_at_once():
    """The panel used to take one name. The question before an import is
    plural: which of these does CAMDS already have?"""
    app = QApplication.instance() or QApplication([])
    tab = CamdsTab()
    submitted = []
    tab.worker = SimpleNamespace(stopping=threading.Event(),
                                 submit=lambda *args: submitted.append(args))
    tab._ready()
    tab.parsed_root = {"node_type": "COMPONENT", "name": "Part", "part_number": "1274478538",
                       "children": [{"node_type": "MATERIAL", "name": "Cu99",
                                     "material_number": None, "children": []}]}
    tab.search_kind.setCurrentText("Material")
    tab.search()
    tab.search()   # one CAMDS operation at a time
    assert len(submitted) == 1
    action, request = submitted[0][0], submitted[0][1]
    assert action == "search"
    assert request["kind"] == "Material"
    assert [item.name for item in request["items"]] == ["Cu99"]
    assert not tab.forms.isEnabled()
    tab._result({"kind": "search", "columns": ["Looked for"], "rows": [["Cu99"]],
                 "note": "1 of 1"})
    assert tab.results.item(0, 0).text() == "Cu99"
    assert tab.forms.isEnabled()
    tab.worker = None
    tab.close()


def test_a_component_search_uses_the_pasted_list_when_asked_to():
    app = QApplication.instance() or QApplication([])
    tab = CamdsTab()
    submitted = []
    tab.worker = SimpleNamespace(stopping=threading.Event(),
                                 submit=lambda *args: submitted.append(args))
    tab._ready()
    tab.search_kind.setCurrentText("Component")
    tab.search_source.setCurrentText("From a pasted list")
    tab.pasted.setPlainText("1274478538, 1274478539")
    tab.search()
    assert [item.number for item in submitted[0][1]["items"]] == ["1274478538", "1274478539"]
    tab.worker = None
    tab.close()


def test_a_search_with_nothing_to_look_up_says_so_instead_of_asking_camds():
    app = QApplication.instance() or QApplication([])
    tab = CamdsTab()
    submitted = []
    tab.worker = SimpleNamespace(stopping=threading.Event(),
                                 submit=lambda *args: submitted.append(args))
    tab._ready()
    tab.search_kind.setCurrentText("Component")
    tab.search_source.setCurrentText("From a pasted list")
    tab.search()
    assert submitted == []
    assert "Paste some component numbers" in tab.status.text()

    tab.search_source.setCurrentText("From the parsed report")
    tab.parsed_root = None
    tab.search()
    assert submitted == []
    assert "Parse an IMDS PDF" in tab.status.text()
    tab.worker = None
    tab.close()


def test_a_failure_locks_the_controls_but_never_traps_the_operator():
    app = QApplication.instance() or QApplication([])
    tab = CamdsTab()
    tab.worker = SimpleNamespace(stopping=threading.Event())
    # The failure happened in a window that is still open, which is the state
    # the operator is actually in when they need a way out.
    tab.browser_open = True
    tab._failed("Field missing", True)
    assert not tab.forms.isEnabled()
    assert tab.close_button.isEnabled(), "closing the window is the way out"
    assert "partially filled" in tab.status.text()
    tab.worker = None
    tab.close()




def test_a_question_from_the_run_is_answered_through_the_control():
    """The run waits on the answer, so the dialog is modal and the answer goes
    back the same way Pause and Stop do."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from camds_imds_importer.camds.import_control import ImportControl
    from camds_imds_importer.ui.camds_tab import CamdsTab

    QApplication.instance() or QApplication([])
    tab = CamdsTab()
    control = ImportControl()
    tab.worker = type("W", (), {"control": control})()
    shown = []

    def press_skip(self):
        shown.append(self.text())
        # The accepting button is the one the run treats as "continue".
        for button in self.buttons():
            if self.buttonRole(button) == QMessageBox.ButtonRole.AcceptRole:
                self.setProperty("pressed", button)
                return 0
        return 0

    QMessageBox.exec = press_skip
    QMessageBox.clickedButton = lambda self: self.property("pressed")
    tab._question("2 substance(s) cannot be identified…")

    assert shown == ["2 substance(s) cannot be identified…"]
    assert control._answered.is_set() and control._answer is True
    tab.worker = None
