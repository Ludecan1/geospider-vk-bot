"""
Отслеживание изменений станций.

Уведомление формируется только при смене устойчивого состояния
«работает» ⇄ «не работает». Промежуточный статус «Соединяется» и
обновление одного лишь времени StatusUpdate событий не создают.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from config import Settings
from geospider import Station, fetch_all_stations, stable_state
from storage import load_station_state, save_station_state

logger = logging.getLogger(__name__)

_check_lock = asyncio.Lock()
_cache: list[Station] = []
_cache_time: float = 0.0
CACHE_MAX_AGE_SECONDS = 120.0


@dataclass(frozen=True)
class StationEvent:
    station: Station
    new_state: str  # STATE_UP / STATE_DOWN


def diff_stations(
    stations: list[Station], previous: dict[str, dict[str, Any]]
) -> tuple[list[StationEvent], dict[str, dict[str, Any]]]:
    events: list[StationEvent] = []
    current: dict[str, dict[str, Any]] = {}

    for station in stations:
        old = previous.get(station.key)
        cur_state = stable_state(station.status_code)

        if not old:
            new_state = cur_state
        else:
            # Старый формат файла без поля state — выводим из прошлого кода.
            if "state" in old:
                prev_state = old.get("state")
            else:
                prev_state = stable_state(int(old.get("status_code", -1)))

            if cur_state is None:
                new_state = prev_state  # «Соединяется» — состояние не меняем
            else:
                new_state = cur_state
                if prev_state is not None and prev_state != cur_state:
                    events.append(StationEvent(station, cur_state))

        current[station.key] = {
            "status_code": station.status_code,
            "status_update": station.status_update,
            "state": new_state,
        }

    # Станции, временно пропавшие из ответа API, не забываем.
    for key, value in previous.items():
        current.setdefault(key, value)

    return events, current


async def check_for_changes(settings: Settings) -> list[StationEvent]:
    """Опрос API + сравнение с сохранённым состоянием. Безопасно вызывать параллельно."""
    global _cache, _cache_time
    async with _check_lock:
        stations = await fetch_all_stations(settings)
        _cache, _cache_time = stations, time.monotonic()

        previous = load_station_state()
        events, current = diff_stations(stations, previous)

        if not previous:
            logger.info("Первый опрос: сохранено %s станций без уведомлений", len(current))
        elif events:
            logger.info(
                "Изменений работает/не работает: %s (%s)",
                len(events),
                ", ".join(f"{e.station.site_code}→{e.new_state}" for e in events),
            )

        save_station_state(current)
        return events


async def get_stations(settings: Settings) -> list[Station]:
    """Последний список станций из кэша фонового опроса или свежий запрос."""
    global _cache, _cache_time
    if _cache and time.monotonic() - _cache_time < CACHE_MAX_AGE_SECONDS:
        return _cache
    stations = await fetch_all_stations(settings)
    _cache, _cache_time = stations, time.monotonic()
    return stations
