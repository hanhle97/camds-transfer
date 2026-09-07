"""The status light, and where the CAMDS checks moved to.

The status line used to carry its state as a glyph inside the sentence - a
tick, a bullet, a warning sign - which reads as punctuation and has no colour.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from camds_imds_importer.core.state_machine import AppState  # noqa: E402
from camds_imds_importer.ui.main_window import LAMP_FOR_STATE  # noqa: E402
from camds_imds_importer.ui.status_light import Lamp, StatusLight  # noqa: E402


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_state_is_the_lamp_and_not_a_character_in_the_sentence(app):
    light = StatusLight("Status: Ready", Lamp.OK)
    assert light.lamp is Lamp.OK
    assert light.text() == "Status: Ready"
    assert not any(glyph in light.text() for glyph in "●✓⚠✕◉"), light.text()


def test_colour_is_never_the_only_carrier(app):
    """A tooltip says in words what the colour says, for anyone who cannot
    tell the two apart or is reading a screenshot in grey."""
    light = StatusLight("CAMDS: Session expired", Lamp.WARN)
    assert light.toolTip() == "Warn: CAMDS: Session expired"


def test_every_lamp_has_a_distinct_colour():
    from camds_imds_importer.ui.status_light import COLOURS

    assert set(COLOURS) == set(Lamp)
    assert len(set(COLOURS.values())) == len(Lamp)


def test_every_application_state_reads_as_a_lamp():
    """A state missing from the table is not an error - it means work in
    progress - but a resting state that reads as busy would be wrong."""
    resting = {AppState.PARSED, AppState.READY, AppState.COMPLETED,
               AppState.CAMDS_AUTHENTICATED, AppState.NO_DOCUMENT}
    for state in resting:
        assert LAMP_FOR_STATE[state] is not Lamp.BUSY, state
    assert LAMP_FOR_STATE[AppState.FAILED] is Lamp.ERROR
    assert LAMP_FOR_STATE[AppState.PAUSED] is Lamp.WARN


def test_the_removed_buttons_are_gone_but_their_work_is_not(app):
    """Checking substances is part of Validate; recording the wizard is a menu
    item. Neither may simply have been deleted."""
    from camds_imds_importer.ui.camds_tab import CamdsTab
    from camds_imds_importer.workers.operations_worker import ACTION_POLICY

    tab = CamdsTab()
    assert not hasattr(tab, "api_button")
    assert not hasattr(tab, "check_substances_button")
    assert not hasattr(tab, "discover_button")
    assert callable(tab.check_substances) and callable(tab.discover_classifications)
    assert "check_substances" in ACTION_POLICY and "discover_classifications" in ACTION_POLICY
    assert "api_check" not in ACTION_POLICY, "an action nothing can dispatch is dead"


def test_the_substance_check_says_why_it_cannot_run(app):
    from camds_imds_importer.ui.camds_tab import CamdsTab

    tab = CamdsTab()
    assert tab.can_check_substances() == "no parsed IMDS document"
    tab.parsed_root = {"uid": "r", "node_type": "COMPONENT", "name": "x", "children": []}
    assert tab.can_check_substances() == "no signed-in CAMDS session"
    assert tab.check_substances() is False
    assert "no signed-in CAMDS session" in tab.status.text()


def test_the_connection_lamp_only_ever_follows_the_operations_browser(app):
    """A credentials test runs in a second browser. It used to light this lamp
    green, which told the operator an import had a session it did not have."""
    from types import SimpleNamespace

    from camds_imds_importer.camds.login import LoginStatus
    from camds_imds_importer.ui.main_window import MainWindow

    window = MainWindow()
    window.connection_label.set("CAMDS: Not connected", Lamp.IDLE)
    window.authenticated = False

    window._login_completed(
        SimpleNamespace(status=LoginStatus.AUTHENTICATED, url="https://catarc.camds.org.cn/",
                        message=""), "someone")

    assert window.authenticated is False, "the operations browser is still anonymous"
    assert window.connection_label.lamp is Lamp.IDLE
    assert "not the operations session" in window.logs_tab.viewer.toPlainText()


def test_signing_in_from_the_menu_uses_the_browser_the_import_uses(app):
    """Otherwise the session would be established where nothing can spend it."""
    import inspect

    from camds_imds_importer.ui.main_window import MainWindow

    source = inspect.getsource(MainWindow.login_from_menu)
    assert "self.camds_tab" in source
    assert "CamdsBrowser" not in source, "a second browser would be a separate session"


def _window(app):
    from camds_imds_importer.ui.main_window import MainWindow
    return MainWindow()


def test_the_stage_never_contradicts_the_status(app):
    """"Status: Ready" beside "Current stage: Validating" was two labels
    describing one thing from two sources, and one of them was stale."""
    from camds_imds_importer.core.state_machine import AppState

    window = _window(app)
    window.state_machine.transition(AppState.DOCUMENT_LOADED)
    window.state_machine.transition(AppState.PARSING)
    window.state_machine.transition(AppState.PARSED)
    window.state_machine.transition(AppState.VALIDATING)
    window._set_stage("VALIDATING")
    assert "Validating" in window.stage_label.text()

    window.state_machine.transition(AppState.READY)
    assert window.status_label.text() == "Status: Ready"
    assert window.stage_label.text() == "Current stage: Ready"


def test_a_worker_still_narrates_while_work_is_running(app):
    """At rest the state wins, but during work the finer stage is the useful
    one and must not be flattened to the state's name."""
    from camds_imds_importer.core.state_machine import AppState

    window = _window(app)
    window.state_machine.transition(AppState.DOCUMENT_LOADED)
    window.state_machine.transition(AppState.PARSING)
    window._set_stage("PDF_READING_PAGES")
    assert window.stage_label.text() == "Current stage: Pdf Reading Pages"


def test_every_resting_state_has_a_lamp_that_is_not_busy():
    from camds_imds_importer.ui.main_window import LAMP_FOR_STATE, RESTING_STATES

    for state in RESTING_STATES:
        assert state in LAMP_FOR_STATE, state
        assert LAMP_FOR_STATE[state] is not Lamp.BUSY, state
