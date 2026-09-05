from __future__ import annotations

from enum import StrEnum


class CamdsAction(StrEnum):
    OPEN = "OPEN"
    SEARCH = "SEARCH"
    READ = "READ"
    SET_FIELD = "SET_FIELD"
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    SAVE_DRAFT = "SAVE_DRAFT"
    DELETE = "DELETE"
    SEND = "SEND"
    PROPOSE = "PROPOSE"
    SUBMIT = "SUBMIT"


SENSITIVE_ACTIONS = frozenset({CamdsAction.DELETE, CamdsAction.SEND, CamdsAction.PROPOSE, CamdsAction.SUBMIT})


class SensitiveActionBlocked(PermissionError):
    """Raised when a destructive/submission action lacks explicit confirmation."""


def require_action_confirmation(action: CamdsAction, *, confirmed: bool = False) -> None:
    if action in SENSITIVE_ACTIONS and not confirmed:
        raise SensitiveActionBlocked(f"{action.value} requires explicit interactive confirmation")
