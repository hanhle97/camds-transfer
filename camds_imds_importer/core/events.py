from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    stage_changed = Signal(str)
    progress_changed = Signal(int)
    operation_progress_changed = Signal(int, int)
    current_node_changed = Signal(str)
    node_started = Signal(str)
    node_completed = Signal(str)
    node_failed = Signal(str, str)
    log_event = Signal(dict)
    warning = Signal(str)
    error = Signal(str)
    completed = Signal()
