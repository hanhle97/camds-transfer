"""Cooperative pause/stop and structured progress for the recursive tree import.

Pause and Stop are honoured only *between* whole CAMDS steps. Interrupting a
Create/Save mid-flight could leave an allocated ID with no journal entry, so the
control is checked at step boundaries and never inside an operation.
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field


def brief(message, lines=6, width=600):
    """Shorten a message for the UI.

    A Playwright timeout carries a full accessibility snapshot of the page,
    which is hundreds of lines and floods every label it reaches. The whole
    text still goes to the journal and the log.
    """
    text = " ".join(str(message).split(chr(10))[:lines]) if lines == 1 else chr(10).join(
        str(message).splitlines()[:lines])
    return text if len(text) <= width else text[:width].rstrip() + " ... (truncated)"


class ImportStopped(RuntimeError):
    """The operator stopped the import between two steps."""


@dataclass(frozen=True, slots=True)
class NodeProgress:
    """One import step, addressed to the UI rather than to the journal."""

    event: str
    message: str
    uid: str | None = None
    name: str | None = None
    kind: str | None = None
    path: tuple[str, ...] = ()
    field_label: str | None = None
    completed: int = 0
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    elapsed_seconds: float = 0.0
    eta_seconds: float | None = None
    ref: tuple[str, str] | None = None

    @property
    def parent_path(self) -> str:
        return " / ".join(self.path) if self.path else "-"

    @property
    def counter(self) -> str:
        return f"{self.completed} / {self.total}" if self.total else str(self.completed)


class ImportControl:
    """Thread-safe Pause/Resume/Stop shared between the Qt thread and the loop."""

    def __init__(self) -> None:
        self._resume = threading.Event()
        self._resume.set()
        self._stop = threading.Event()

    def reset(self) -> None:
        """Re-arm for a new import without swapping the object the UI already holds."""
        self._stop.clear()
        self._resume.set()

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def stop(self) -> None:
        self._stop.set()
        # Release a paused import so it can observe the stop instead of hanging.
        self._resume.set()

    @property
    def paused(self) -> bool:
        return not self._resume.is_set() and not self._stop.is_set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    async def wait(self, on_pause=None, on_resume=None) -> None:
        """Block at a step boundary while paused; raise if stopping."""
        if self._stop.is_set():
            raise ImportStopped("Import stopped by operator between steps; saved objects are unchanged.")
        if not self._resume.is_set():
            if on_pause:
                on_pause()
            while not self._resume.is_set():
                await asyncio.sleep(0.1)
            if not self._stop.is_set() and on_resume:
                on_resume()
        if self._stop.is_set():
            raise ImportStopped("Import stopped by operator between steps; saved objects are unchanged.")


class Reporter:
    """Turns import steps into NodeProgress with counters and an ETA."""

    MESSAGES = {
        "resumed": "Resuming from journal",
        "save_requested": "Saving draft",
        "save_returned_pending_readback": "Checking next step",
        "create_material_requested": "Creating Material",
        "material_id_allocated": "Material ID assigned",
        "add_substance_requested": "Adding Substance",
        "existing_material_verified": "Existing Material verified",
        "material_readback_verified": "Saved Material verified",
        "skipped_completed": "Already saved; skipping",
        "create_parent_requested": "Creating parent Component",
        "parent_id_allocated": "Parent ID assigned",
        "add_child_requested": "Adding child Component",
        "add_semicomponent_requested": "Adding Semicomponent",
        "attach_material_requested": "Attaching Material",
        "verify_started": "Reading back saved tree",
        "complete_readback_verified": "Saved tree verified",
        "paused": "Paused between steps",
        "field": "Filling field",
    }

    def __init__(self, total: int, emit) -> None:
        self.total = total
        self.emit = emit
        self.completed = 0
        self.succeeded = 0
        self.failed = 0
        self.started_at = time.monotonic()
        self.path: tuple[str, ...] = ()
        self.uid: str | None = None
        self.name: str | None = None
        self.kind: str | None = None

    def _eta(self) -> float | None:
        if not self.completed or not self.total:
            return None
        elapsed = time.monotonic() - self.started_at
        rate = self.completed / elapsed if elapsed else 0.0
        return (self.total - self.completed) / rate if rate else None

    def step(self, uid: str | None, name: str | None, kind: str | None, path=()) -> None:
        """Enter a new node; counters advance only when the step completes."""
        self.uid, self.name, self.kind, self.path = uid, name, kind, tuple(path)

    def done(self) -> None:
        self.completed += 1
        self.succeeded += 1

    def fail(self) -> None:
        self.failed += 1

    def __call__(self, event: str, *, field_label: str | None = None, ref=None, name: str | None = None) -> None:
        label = self.MESSAGES.get(event, event.replace("_", " ").capitalize())
        display = name or self.name
        self.emit(NodeProgress(
            event=event,
            message=label + (": " + display if display else ""),
            uid=self.uid, name=display, kind=self.kind, path=self.path,
            field_label=field_label,
            completed=self.completed, total=self.total,
            succeeded=self.succeeded, failed=self.failed,
            elapsed_seconds=time.monotonic() - self.started_at,
            eta_seconds=self._eta(),
            ref=tuple(ref) if ref else None,
        ))
