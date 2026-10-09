from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import DATA_DIR, STATE_FILE, SUBSCRIBERS_FILE, USER_SETTINGS_FILE

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


EVENTS_BOTH = "both"
EVENTS_DOWN = "down"
EVENTS_UP = "up"
EVENT_MODES = (EVENTS_BOTH, EVENTS_DOWN, EVENTS_UP)


@dataclass
class UserSettings:
    """Персональные настройки подписчика."""

    radius_km: float
    # None — все станции в радиусе; иначе — только эти коды станций (радиус не учитывается).
    stations: set[str] | None = None
    events: str = EVENTS_BOTH

    def to_json(self) -> dict[str, Any]:
        return {
            "radius_km": self.radius_km,
            "stations": sorted(self.stations) if self.stations is not None else None,
            "events": self.events,
        }


def _default_radius() -> float:
    try:
        return float(os.getenv("RADIUS_KM", "75"))
    except ValueError:
        return 75.0


def _load_all_user_settings() -> dict[str, Any]:
    data = _read_json(USER_SETTINGS_FILE, {})
    return data if isinstance(data, dict) else {}


def load_user_settings(peer_id: int) -> UserSettings:
    raw = _load_all_user_settings().get(str(peer_id))
    us = UserSettings(radius_km=_default_radius())
    if not isinstance(raw, dict):
        return us
    try:
        us.radius_km = float(raw.get("radius_km", us.radius_km))
    except (TypeError, ValueError):
        pass
    st = raw.get("stations")
    if isinstance(st, list):
        us.stations = {str(x) for x in st}
    ev = raw.get("events")
    if ev in EVENT_MODES:
        us.events = ev
    return us


def save_user_settings(peer_id: int, us: UserSettings) -> None:
    data = _load_all_user_settings()
    data[str(peer_id)] = us.to_json()
    _write_json(USER_SETTINGS_FILE, data)


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
