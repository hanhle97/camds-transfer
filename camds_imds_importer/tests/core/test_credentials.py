from unittest.mock import patch

import pytest

from camds_imds_importer.core.config import save_config
from camds_imds_importer.core.credentials import CredentialManager


def test_save_read_clear_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[tuple[str, str], str] = {}
    monkeypatch.delenv("CAMDS_USERNAME", raising=False)
    monkeypatch.delenv("CAMDS_PASSWORD", raising=False)
    with (
        patch("keyring.set_password", side_effect=lambda service, key, value: store.__setitem__((service, key), value)),
        patch("keyring.get_password", side_effect=lambda service, key: store.get((service, key))),
        patch("keyring.delete_password", side_effect=lambda service, key: store.pop((service, key), None)),
    ):
        manager = CredentialManager()
        manager.save_credentials("engineer", "secret-value")
        assert manager.get_credentials().username == "engineer"
        assert manager.get_credentials().password == "secret-value"
        manager.clear_credentials()
        assert manager.get_credentials() is None


def test_save_username_without_password(monkeypatch: pytest.MonkeyPatch) -> None:
    store: dict[tuple[str, str], str] = {}
    monkeypatch.delenv("CAMDS_USERNAME", raising=False)
    with (
        patch("keyring.set_password", side_effect=lambda service, key, value: store.__setitem__((service, key), value)),
        patch("keyring.get_password", side_effect=lambda service, key: store.get((service, key))),
    ):
        manager = CredentialManager()
        manager.save_username("engineer")
        assert manager.get_username() == "engineer"
        assert manager.get_password() is None


def test_environment_has_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMDS_USERNAME", "environment-user")
    monkeypatch.setenv("CAMDS_PASSWORD", "environment-secret")
    with patch("keyring.get_password", return_value="keyring-value"):
        credentials = CredentialManager().get_credentials()
    assert credentials.username == "environment-user"
    assert credentials.password == "environment-secret"


def test_password_cannot_be_written_to_config(tmp_path) -> None:
    with pytest.raises(ValueError):
        save_config(tmp_path / "config.yaml", {"camds": {"password": "must-not-persist"}})
    assert not (tmp_path / "config.yaml").exists()
