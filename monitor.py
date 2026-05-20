from __future__ import annotations

import logging
from typing import Any

from geospider import Station, fetch_stations, format_change_message
from config import Settings
from storage import load_station_state, save_station_state

logger = logging.getLogger(__name__)


def diff_stations(
    stations: list[Station], previous: dict[str, dict[str, Any]]
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    changes: list[str] = []
    current: dict[str, dict[str, Any]] = {}

    for station in stations:
        current[station.key] = station.to_state_value()
        old = previous.get(station.key)
        if not old:
            continue

        old_code = int(old.get("status_code", -1))
        old_update = str(old.get("status_update", ""))
        if old_code != station.status_code or old_update != station.status_update:
            changes.append(format_change_message(station, old_code, old_update))

    return changes, current


async def check_for_changes(settings: Settings) -> list[str]:
    stations = await fetch_stations(settings)
    previous = load_station_state()
    changes, current = diff_stations(stations, previous)

    if not previous:
        logger.info("Первый опрос: сохранено %s станций без уведомлений", len(current))
    elif changes:
        logger.info("Обнаружено изменений: %s", len(changes))

    save_station_state(current)
    return changes
