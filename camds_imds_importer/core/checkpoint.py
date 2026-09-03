from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(slots=True)
class RunState:
    job_id: str
    source_pdf: str
    imds_id: str | None = None
    camds_record_id: str | None = None
    stage: str = "IDLE"
    total_nodes: int = 0
    completed_nodes: int = 0
    current_node: str | None = None
    completed_node_ids: list[str] = field(default_factory=list)
    failed_node_ids: list[str] = field(default_factory=list)


def save_checkpoint(path: Path, state: RunState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(asdict(state), stream, indent=2)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
