import pytest

from camds_imds_importer.core.state_machine import AppState, ApplicationStateMachine


def test_document_state_transitions() -> None:
    machine = ApplicationStateMachine()
    for state in (AppState.DOCUMENT_LOADED, AppState.PARSING, AppState.PARSED, AppState.VALIDATING, AppState.READY):
        machine.transition(state)
    assert machine.state == AppState.READY


def test_invalid_transition_is_rejected() -> None:
    with pytest.raises(ValueError):
        ApplicationStateMachine().transition(AppState.IMPORTING)
