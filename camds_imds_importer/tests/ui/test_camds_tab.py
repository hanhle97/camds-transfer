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


def _tab():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from camds_imds_importer.ui.camds_tab import CamdsTab

    QApplication.instance() or QApplication([])
    return CamdsTab()


def test_search_results_can_be_written_to_a_spreadsheet(tmp_path, monkeypatch):
    """76 parts read off the screen is not 76 parts anyone can sort or send on."""
    from zipfile import ZipFile

    from PySide6.QtWidgets import QFileDialog

    from camds_imds_importer.camds import survey

    tab = _tab()
    assert not tab.save_button.isEnabled(), "nothing to save before a search"

    tab._result({"kind": "search", "identity": "", "note": "2 of 2 found",
                 "columns": list(survey.HEADINGS),
                 "rows": [["Bracket", "1234567890", "CA_5_9", "2", "1", "2026-09-01"],
                          ["Cover", "1234567891", "not found", "-", "0", "-"]]})
    assert tab.save_button.isEnabled(), "results are worth keeping"

    target = tmp_path / "results.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(target), "")))
    tab.save_results()

    assert target.is_file()
    with ZipFile(target) as book:
        sheet = book.read("xl/worksheets/sheet1.xml").decode("utf-8")
    assert "Looked for" in sheet, "the headings travel with the rows"
    assert "1234567890" in sheet and "CA_5_9" in sheet
    assert "not found" in sheet, "what CAMDS does not have is part of the answer"
    tab.close()


def test_nothing_is_written_when_the_save_is_cancelled(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog

    from camds_imds_importer.camds import survey

    tab = _tab()
    tab._result({"kind": "search", "identity": "", "note": "1 found",
                 "columns": list(survey.HEADINGS),
                 "rows": [["Bracket", "1234567890", "CA_5_9", "2", "1", "2026-09-01"]]})
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    tab.save_results()
    assert not list(tmp_path.iterdir()), "a cancelled save writes nothing"
    tab.close()


def test_the_suggested_name_says_what_and_when(monkeypatch):
    """A folder of "results.xlsx" tells nobody which search each one was."""
    from PySide6.QtWidgets import QFileDialog

    from camds_imds_importer.camds import survey

    tab = _tab()
    tab.search_kind.setCurrentText("Component")
    tab._result({"kind": "search", "identity": "", "note": "", "columns": list(survey.HEADINGS),
                 "rows": [["Bracket", "1234567890", "CA_5_9", "2", "1", "2026-09-01"]]})
    offered = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda parent, title, name, filt: (offered.append(name), ("", ""))[1]))
    tab.save_results()

    assert offered and offered[0].startswith("camds-components-")
    assert offered[0].endswith(".xlsx")
    tab.close()
