from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "browser": {"headless": False, "slow_mo": 50},
    "camds": {
        "base_url": "https://catarc.camds.org.cn",
        "login_url": "https://catarc.camds.org.cn/#/login",
        "username_saved": False,
        "use_keyring": True,
    },
    "automation": {"mode": "SAVE_DRAFT", "checkpoint_each_node": True, "max_retries": 3},
}


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return DEFAULT_CONFIG.copy()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {**DEFAULT_CONFIG, **data}


def save_config(path: Path, config: dict[str, Any]) -> None:
    if any(key.lower() in {"password", "passwd", "pwd"} for key in config.get("camds", {})):
        raise ValueError("Passwords may not be written to configuration")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
