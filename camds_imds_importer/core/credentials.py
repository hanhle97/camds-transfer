from __future__ import annotations

import os
from dataclasses import dataclass

import keyring
from keyring.errors import PasswordDeleteError


@dataclass(frozen=True, slots=True)
class Credentials:
    username: str
    password: str


class CredentialManager:
    SERVICE_NAME = "CAMDS_IMDS_IMPORTER"
    USERNAME_KEY = "camds_username"

    def save_username(self, username: str) -> None:
        if not username.strip():
            raise ValueError("Username is required")
        keyring.set_password(self.SERVICE_NAME, self.USERNAME_KEY, username.strip())

    def save_credentials(self, username: str, password: str) -> None:
        if not username.strip() or not password:
            raise ValueError("Username and password are required")
        self.save_username(username)
        keyring.set_password(self.SERVICE_NAME, username.strip(), password)

    def get_username(self) -> str | None:
        return os.getenv("CAMDS_USERNAME") or keyring.get_password(self.SERVICE_NAME, self.USERNAME_KEY)

    def get_password(self, username: str | None = None) -> str | None:
        if os.getenv("CAMDS_PASSWORD"):
            return os.environ["CAMDS_PASSWORD"]
        resolved_username = username or self.get_username()
        return keyring.get_password(self.SERVICE_NAME, resolved_username) if resolved_username else None

    def get_credentials(self) -> Credentials | None:
        username = self.get_username()
        password = self.get_password(username)
        return Credentials(username, password) if username and password else None

    def clear_credentials(self) -> None:
        username = keyring.get_password(self.SERVICE_NAME, self.USERNAME_KEY)
        for account in filter(None, (username, self.USERNAME_KEY)):
            try:
                keyring.delete_password(self.SERVICE_NAME, account)
            except PasswordDeleteError:
                pass
