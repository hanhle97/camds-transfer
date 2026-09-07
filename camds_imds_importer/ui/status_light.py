"""A coloured light beside a line of status text.

The states were glyphs in the text itself - a tick, a bullet, a warning sign -
which read as punctuation at a glance and carried no colour. A light is read
before the sentence is, which is the point of a status line.
"""
from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

DIAMETER = 10


class Lamp(StrEnum):
    IDLE = "IDLE"        # nothing has happened yet
    BUSY = "BUSY"        # work in progress
    OK = "OK"            # connected, or finished cleanly
    WARN = "WARN"        # needs attention but nothing is broken
    ERROR = "ERROR"      # the last thing tried failed


# Chosen to stay apart for the common red/green colour blindness: the amber and
# the red differ in lightness, not only in hue.
COLOURS = {
    Lamp.IDLE: "#7d8590",
    Lamp.BUSY: "#3b82f6",
    Lamp.OK: "#2ea043",
    Lamp.WARN: "#d29922",
    Lamp.ERROR: "#f85149",
}


class _Dot(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.lamp = Lamp.IDLE
        self.setFixedSize(QSize(DIAMETER + 4, DIAMETER + 4))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = QColor(COLOURS[self.lamp])
        painter.setBrush(colour)
        # A darker rim keeps the dot visible on both light and dark themes.
        painter.setPen(colour.darker(160))
        painter.drawEllipse(2, 2, DIAMETER, DIAMETER)


class StatusLight(QWidget):
    """A lamp and a label that always describe the same thing."""

    def __init__(self, text: str = "", lamp: Lamp = Lamp.IDLE, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._dot = _Dot(self)
        self._label = QLabel(text, self)
        row.addWidget(self._dot, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._label, 1)
        self.set(text, lamp)

    def set(self, text: str, lamp: Lamp = Lamp.IDLE) -> None:
        self._dot.lamp = lamp
        self._dot.update()
        self._label.setText(text)
        # The colour is not the only carrier: the tooltip says it in words.
        self.setToolTip(f"{lamp.value.title()}: {text}")

    @property
    def lamp(self) -> Lamp:
        return self._dot.lamp

    def text(self) -> str:
        return self._label.text()
