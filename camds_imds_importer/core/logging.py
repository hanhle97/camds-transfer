from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SENSITIVE_FIELDS = {"password", "passwd", "pwd", "cookie", "cookies", "authorization", "token", "access_token", "refresh_token"}


def sanitize_log_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: "***REDACTED***" if str(key).lower() in SENSITIVE_FIELDS else sanitize_log_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_log_data(item) for item in value]
    return value


class JsonlLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: str, **data: Any) -> None:
        record = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event, **sanitize_log_data(data)}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        logging.getLogger(__name__).info("%s", event)
