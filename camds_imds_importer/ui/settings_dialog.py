from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout

from ..core.credentials import CredentialManager, Credentials


class SettingsDialog(QDialog):
    test_login_requested = Signal(object)

    def __init__(self, credentials: CredentialManager, parent=None) -> None:
        super().__init__(parent)
        self.credentials = credentials
        self.setWindowTitle("CAMDS Account")
        self.username = QLineEdit(credentials.get_username() or "")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.remember_username = QCheckBox("Remember username")
        self.remember_password = QCheckBox("Remember password securely")
        show_password = QCheckBox("Show password")
        show_password.toggled.connect(lambda checked: self.password.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password))
        form = QFormLayout()
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        form.addRow("", self.remember_username)
        form.addRow("", self.remember_password)
        form.addRow("", show_password)
        test_login = QPushButton("Test Login")
        clear = QPushButton("Clear Credentials")
        test_login.clicked.connect(self._test_login)
        clear.clicked.connect(self._clear)
        action_row = QHBoxLayout()
        action_row.addWidget(test_login)
        action_row.addWidget(clear)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(action_row)
        layout.addWidget(buttons)

    def _entered(self) -> Credentials | None:
        username, password = self.username.text().strip(), self.password.text()
        if username and password:
            return Credentials(username, password)
        return self.credentials.get_credentials()

    def _test_login(self) -> None:
        entered = self._entered()
        if entered:
            self.test_login_requested.emit(entered)

    def _save(self) -> None:
        entered = self._entered()
        if entered and self.remember_password.isChecked():
            self.credentials.save_credentials(entered.username, entered.password)
        elif self.remember_username.isChecked() and self.username.text().strip():
            self.credentials.save_username(self.username.text())
        self.accept()

    def _clear(self) -> None:
        self.credentials.clear_credentials()
        self.username.clear()
        self.password.clear()
