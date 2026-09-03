from PySide6.QtWidgets import QLabel, QVBoxLayout, QDialog


class LoginDialog(QDialog):
    def __init__(self, message: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("CAMDS Login")
        QVBoxLayout(self).addWidget(QLabel(message))
