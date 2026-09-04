from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QObject, Signal


class AppState(StrEnum):
    NO_DOCUMENT = "NO_DOCUMENT"
    DOCUMENT_LOADED = "DOCUMENT_LOADED"
    PARSING = "PARSING"
    PARSED = "PARSED"
    VALIDATING = "VALIDATING"
    READY = "READY"
    CAMDS_LOGIN_REQUIRED = "CAMDS_LOGIN_REQUIRED"
    CAMDS_AUTHENTICATED = "CAMDS_AUTHENTICATED"
    IMPORTING = "IMPORTING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


ALLOWED_TRANSITIONS: dict[AppState, set[AppState]] = {
    AppState.NO_DOCUMENT: {AppState.DOCUMENT_LOADED, AppState.CAMDS_LOGIN_REQUIRED, AppState.CAMDS_AUTHENTICATED},
    AppState.DOCUMENT_LOADED: {AppState.PARSING, AppState.NO_DOCUMENT, AppState.CAMDS_LOGIN_REQUIRED, AppState.CAMDS_AUTHENTICATED},
    AppState.PARSING: {AppState.PARSED, AppState.FAILED, AppState.CAMDS_LOGIN_REQUIRED},
    AppState.PARSED: {AppState.VALIDATING, AppState.DOCUMENT_LOADED, AppState.CAMDS_LOGIN_REQUIRED, AppState.CAMDS_AUTHENTICATED},
    AppState.VALIDATING: {AppState.READY, AppState.FAILED, AppState.CAMDS_LOGIN_REQUIRED},
    AppState.READY: {AppState.CAMDS_LOGIN_REQUIRED, AppState.CAMDS_AUTHENTICATED, AppState.IMPORTING, AppState.DOCUMENT_LOADED},
    AppState.CAMDS_LOGIN_REQUIRED: {AppState.CAMDS_AUTHENTICATED, AppState.FAILED, AppState.DOCUMENT_LOADED},
    AppState.CAMDS_AUTHENTICATED: {AppState.IMPORTING, AppState.DOCUMENT_LOADED, AppState.CAMDS_LOGIN_REQUIRED, AppState.READY},
    AppState.IMPORTING: {AppState.PAUSED, AppState.COMPLETED, AppState.FAILED},
    AppState.PAUSED: {AppState.IMPORTING, AppState.FAILED},
    AppState.COMPLETED: {AppState.DOCUMENT_LOADED},
    AppState.FAILED: {AppState.DOCUMENT_LOADED, AppState.CAMDS_LOGIN_REQUIRED},
}


class ApplicationStateMachine(QObject):
    state_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._state = AppState.NO_DOCUMENT

    @property
    def state(self) -> AppState:
        return self._state

    def transition(self, new_state: AppState) -> None:
        if new_state == self._state:
            return
        if new_state not in ALLOWED_TRANSITIONS[self._state]:
            raise ValueError(f"Invalid state transition: {self._state} -> {new_state}")
        self._state = new_state
        self.state_changed.emit(new_state.value)
