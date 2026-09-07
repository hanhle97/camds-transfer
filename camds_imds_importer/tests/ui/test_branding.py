"""The window icon.

A missing icon file used to be invisible: the app simply showed the Python
interpreter's icon and nothing said why. These check that the file ships, that
it is a real icon Qt can read, and that a broken one never stops the app.
"""
import os

import pytest

from camds_imds_importer.ui import branding

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_the_icon_ships_with_the_package():
    """It has to travel with a clone, or a second machine has no icon."""
    assert branding.ICON_PATH.is_file(), branding.ICON_PATH
    # 00 00 01 00 is the ICO signature; a stray PNG renamed .ico would not do.
    assert branding.ICON_PATH.read_bytes()[:4] == b"\x00\x00\x01\x00"


def test_qt_can_actually_read_it():
    """The file existing is not the same as Qt being able to draw it."""
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    icon = QIcon(str(branding.ICON_PATH))
    assert not icon.isNull()
    assert icon.availableSizes(), "no usable size in the icon"


def test_the_application_is_given_the_icon():
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    branding.apply(application)
    assert not application.windowIcon().isNull()


def test_a_missing_icon_does_not_stop_the_app(monkeypatch, tmp_path):
    """Cosmetics must never be a reason the app will not start."""
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(branding, "ICON_PATH", tmp_path / "gone.ico")
    branding.apply(application)  # must not raise
