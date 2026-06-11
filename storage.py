from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from config import DATA_DIR, STATE_FILE, SUBSCRIBERS_FILE

logger = logging.getLogger(__name__)

SEED_FILE = Path(__file__).resolve().parent / "subscribers.seed.json"


def _read_json(path: Path, default: Any) -> Any:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return default
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("Не удалось прочитать %s: %s", path.name, exc)
        return default
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Повреждён %s — сброс к значению по умолчанию", path.name)
        return default


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


def _parse_subscriber_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part:
            ids.add(int(part))
    return ids


def _load_seed_subscriber_ids() -> set[int]:
    env_raw = os.getenv("SUBSCRIBER_IDS", "").strip()
    if env_raw:
        return _parse_subscriber_ids(env_raw)
    if not SEED_FILE.exists():
        return set()
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        return set()
    return {int(x) for x in data}


def ensure_subscribers_seeded() -> int:
    """
    На Bothost data/subscribers.json часто пустой после деплоя.
    Восстанавливаем из SUBSCRIBER_IDS или subscribers.seed.json в репозитории.
    """
    current = load_subscribers()
    if current:
        return len(current)
    seed = _load_seed_subscriber_ids()
    if not seed:
        return 0
    save_subscribers(seed)
    logger.info("Подписчики восстановлены при старте: %s", sorted(seed))
    return len(seed)
