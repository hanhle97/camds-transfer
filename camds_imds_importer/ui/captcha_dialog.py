from __future__ import annotations

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout


class CaptchaDialog(QDialog):
    code_submitted = Signal(str)
    verification_completed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CAMDS verification required")
        self.setModal(False)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("CAMDS requires interactive verification. Complete the slider or read the CAPTCHA in the browser, then enter the code below."))
        self.image = QLabel("Waiting for CAPTCHA image…")
        self.image.setMinimumSize(360, 120)
        self.image.setScaledContents(False)
        layout.addWidget(self.image)
        self.code = QLineEdit()
        self.code.setPlaceholderText("Enter CAPTCHA code (if required)")
        layout.addWidget(self.code)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("I completed verification")
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def set_image(self, image: bytes) -> None:
        buffer = QBuffer()
        buffer.setData(QByteArray(image))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        pixmap = QPixmap()
        pixmap.loadFromData(buffer.data())
        self.image.setPixmap(pixmap.scaled(600, 240))

    def _submit(self) -> None:
        self.code_submitted.emit(self.code.text())
        self.code.clear()
        self.verification_completed.emit()
        self.hide()
