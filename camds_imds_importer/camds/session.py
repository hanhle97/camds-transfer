from __future__ import annotations

from pathlib import Path


def storage_state_if_available(path: Path) -> Path | None:
    return path if path.is_file() and path.stat().st_size > 0 else None
