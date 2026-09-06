"""An open Create editor must not dead-end the session or misreport why."""
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from camds_imds_importer.ui.camds_tab import CamdsTab
from camds_imds_importer.workers.operations_worker import ACTION_POLICY


def tab_with_worker(sent):
    QApplication.instance() or QApplication([])
    tab = CamdsTab()
    tab.worker = SimpleNamespace(stopping=threading.Event(), submit=lambda *a, **k: sent.append(a))
    return tab


def test_open_editor_explains_itself_instead_of_asking_for_work_already_done():
    tab = tab_with_worker([])
    tab.parsed_root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 1, "children": []}
    tab._result({"kind": "create", "identity": "CA_5_1/0.01", "note": "filled"})
    assert tab.editor_open and not tab.import_tree_button.isEnabled()
    tab.review_import()
    message = tab.status.text()
    assert "editor is open" in message
    assert "Leave editor" in message
    assert "Parse" not in message, "the operator has already parsed a PDF and opened a session"
    tab.worker = None
    tab.close()


def test_leaving_the_editor_reopens_the_session_without_closing_the_browser(monkeypatch):
    sent = []
    tab = tab_with_worker(sent)
    tab.parsed_root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 1, "children": []}
    tab._result({"kind": "create", "identity": "CA_5_1/0.01", "note": "filled"})
    assert tab.leave_button.isEnabled()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    tab.leave_editor()
    assert sent == [], "declining the confirmation must not touch the browser"

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    tab.leave_editor()
    assert sent == [("leave_editor", None)]

    tab._result({"kind": "leave_editor", "editor_open": False, "note": "back on Search"})
    assert not tab.editor_open
    assert tab.import_tree_button.isEnabled(), "the tree import must be reachable again"
    assert tab.forms.isEnabled()
    tab.worker = None
    tab.close()


def test_leave_stays_available_after_a_partial_failure():
    tab = tab_with_worker([])
    tab.parsed_root = {"uid": "r", "node_type": "COMPONENT", "name": "Parent", "weight_g": 1, "children": []}
    tab._failed("CAMDS Save validation: mass required", True)
    assert not tab.forms.isEnabled()
    assert not tab.save_button.isEnabled()
    assert tab.leave_button.isEnabled(), "recovery must not require killing the whole session"
    tab.worker = None
    tab.close()


def test_leaving_is_classified_as_a_non_sensitive_navigation():
    assert ACTION_POLICY["leave_editor"].value == "OPEN"


@pytest.mark.parametrize("kind", ["save", "leave_editor"])
def test_only_save_and_leave_are_accepted_while_an_editor_is_open(kind):
    sent = []
    tab = tab_with_worker(sent)
    tab.editor_open = True
    tab._submit(kind, None)
    assert sent == [(kind, None)]
    tab.busy = False
    tab._submit("search", None)
    assert len(sent) == 1, "a search must not run over an open editor"
    tab.worker = None
    tab.close()
