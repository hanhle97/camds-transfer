"""Login, session loss and each import step must move every indicator together."""
import threading
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication

from camds_imds_importer.camds.import_control import ImportControl, NodeProgress
from camds_imds_importer.core.state_machine import AppState
from camds_imds_importer.ui.main_window import MainWindow


def window():
    QApplication.instance() or QApplication([])
    return MainWindow()


def test_authenticated_session_updates_status_connection_stage_and_progress():
    main = window()
    main.camds_tab.session_changed.emit("AUTHENTICATED")
    assert main.authenticated
    assert "Logged in" in main.connection_label.text()
    assert "Camds Authenticated" in main.stage_label.text()
    assert main.state_machine.state == AppState.CAMDS_AUTHENTICATED
    assert "Camds Authenticated" in main.progress_tab.labels["Stage"].text()
    assert main.status_label.text().endswith("Camds Authenticated")
    main.close()


def test_expired_session_downgrades_the_connection_instead_of_staying_logged_in():
    main = window()
    main.camds_tab.session_changed.emit("AUTHENTICATED")
    main.camds_tab.session_changed.emit("EXPIRED")
    assert not main.authenticated
    assert "expired" in main.connection_label.text().lower()
    assert main.state_machine.state == AppState.CAMDS_LOGIN_REQUIRED
    main.close()


def test_import_step_drives_counter_path_field_and_overall_bar():
    main = window()
    event = NodeProgress(event="add_child_requested", message="Adding child Component: Bracket",
                         uid="c1", name="Bracket", kind="COMPONENT", path=("Parent", "Outer"),
                         field_label="Quantity", completed=12, total=148, succeeded=12,
                         elapsed_seconds=30.0, eta_seconds=340.0)
    main.camds_tab.node_progress.emit(event)
    assert main.progress_tab.labels["Completed"].text() == "12 / 148"
    assert main.progress_tab.labels["Parent path"].text() == "Parent / Outer"
    assert main.progress_tab.labels["Field"].text() == "Quantity"
    assert main.progress_tab.labels["Succeeded"].text() == "12"
    assert "340s" in main.progress_tab.labels["ETA"].text()
    assert main.overall.value() == 8
    assert "12 / 148" in main.node_label.text() and "Parent / Outer" in main.node_label.text()
    main.close()


def test_failed_node_is_counted_and_logged_per_node():
    main = window()
    main.camds_tab.node_progress.emit(NodeProgress(
        event="node_failed", message="CAMDS Save validation: mass required",
        uid="c1", name="Bracket", kind="COMPONENT", path=("Parent",), completed=3, total=10, failed=1))
    assert main.progress.errors == 1
    assert "Bracket" in main.logs_tab.viewer.toPlainText()
    assert main.progress_tab.labels["Failed"].text() == "1"
    main.close()


def test_pause_and_resume_track_the_shared_import_control():
    main = window()
    control = ImportControl()
    main.camds_tab.worker = SimpleNamespace(stopping=threading.Event(), control=control)
    main.camds_tab.importing = True
    main._apply_state(main.state_machine.state.value)
    assert main.pause_button.isEnabled() and not main.resume_button.isEnabled()

    main.pause_button.click()
    assert control.paused
    # The importer confirms it actually stopped at a boundary.
    main.camds_tab.node_progress.emit(NodeProgress(event="paused", message="Paused between steps",
                                                   completed=2, total=10))
    assert "Paused" in main.status_label.text()
    main.camds_tab._update()
    assert main.camds_tab.resume_button.isEnabled()
    assert not main.camds_tab.pause_button.isEnabled()
    # A later state refresh must not erase the paused indicator.
    main._apply_state(main.state_machine.state.value)
    assert "Paused" in main.status_label.text()

    main.resume_button.click()
    assert not control.paused
    main.stop_button.click()
    assert control.stopping
    main.camds_tab.worker = None
    main.close()


def test_sign_in_request_from_the_tab_does_not_open_a_second_browser(monkeypatch):
    main = window()
    opened = []
    monkeypatch.setattr(main.credentials, "get_credentials", lambda: SimpleNamespace(username="u", password="p"))
    monkeypatch.setattr(main.camds_tab, "open_session", lambda: opened.append("session"))
    main.camds_tab.login_required.emit()
    assert opened == ["session"], "login must run on the operations session, not a throwaway browser"
    assert main._pending_login is not None
    sent = []
    main.camds_tab.worker = SimpleNamespace(stopping=threading.Event(), submit=lambda *a, **k: sent.append(a))
    main.camds_tab.session_ready.emit()
    assert sent and sent[0][0] == "login"
    assert main._pending_login is None
    main.camds_tab.worker = None
    main.close()
