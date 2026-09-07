"""The window and taskbar icon.

The icon is CAMDS's own mark, taken from `catarc.camds.org.cn/favicon.ico`. It
belongs to CATARC, not to this project: it is used here so the window is easy
to find among several, and it does not make this an official CAMDS tool. The
title bar says "CAMDS IMDS Importer" for the same reason.
"""
from __future__ import annotations

import sys
from pathlib import Path

ICON_PATH = Path(__file__).with_name("assets") / "camds.ico"

# Windows groups taskbar buttons by this id. Without one, a Python process
# shows the interpreter's icon in the taskbar however the window is dressed.
APP_ID = "camds.imds.importer"


def apply(application) -> None:
    """Give the application its icon, on the taskbar as well as the window."""
    from PySide6.QtGui import QIcon

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            # A missing icon is a cosmetic problem; it must not stop the app.
            pass
    if ICON_PATH.is_file():
        application.setWindowIcon(QIcon(str(ICON_PATH)))
