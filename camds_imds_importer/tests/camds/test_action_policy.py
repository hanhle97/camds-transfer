import pytest

from camds_imds_importer.camds.action_policy import CamdsAction, SensitiveActionBlocked, require_action_confirmation


def test_sensitive_actions_require_confirmation() -> None:
    with pytest.raises(SensitiveActionBlocked):
        require_action_confirmation(CamdsAction.DELETE)
    with pytest.raises(SensitiveActionBlocked):
        require_action_confirmation(CamdsAction.SUBMIT)


def test_safe_actions_do_not_require_confirmation() -> None:
    require_action_confirmation(CamdsAction.SEARCH)
    require_action_confirmation(CamdsAction.SET_FIELD)
    require_action_confirmation(CamdsAction.SUBMIT, confirmed=True)
