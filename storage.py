from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import DATA_DIR, STATE_FILE, SUBSCRIBERS_FILE


def _read_json(path: Path, default: Any) -> Any:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _write_json(path: Path, data: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def load_station_state() -> dict[str, dict[str, Any]]:
    data = _read_json(STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_station_state(state: dict[str, dict[str, Any]]) -> None:
    _write_json(STATE_FILE, state)


def load_subscribers() -> set[int]:
    """peer_id получателей для messages.send."""
    data = _read_json(SUBSCRIBERS_FILE, [])
    if not isinstance(data, list):
        return set()
    return {int(x) for x in data}


def save_subscribers(subscribers: set[int]) -> None:
    _write_json(SUBSCRIBERS_FILE, sorted(subscribers))
