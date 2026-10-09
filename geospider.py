"""Опрос API ГЕОСПАЙДЕР и форматирование (текст для VK, без HTML)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from config import Settings

STATUS_LABELS: dict[int, str] = {
    0: "Нет связи",
    1: "Отключена",
    3: "Работает",
    6: "Соединяется",
    50001: "Планируемая",
    50002: "Демонтирована",
}

STATUS_EMOJI: dict[int, str] = {
    0: "🔴",
    1: "⚫",
    3: "🟢",
    6: "🟠",
    50001: "🔵",
    50002: "🟤",
}

MAP_URL = "https://geospider.ru/networkmap"

# Упрощённое состояние для уведомлений: только «работает» / «не работает».
STATE_UP = "up"
STATE_DOWN = "down"

# Промежуточные статусы: по ним уведомления не шлём, ждём устойчивого состояния.
TRANSIENT_CODES: frozenset[int] = frozenset({6})
WORKING_CODES: frozenset[int] = frozenset({3})


def stable_state(code: int) -> str | None:
    """'up' — работает, 'down' — не работает, None — промежуточный статус (соединяется)."""
    if code in WORKING_CODES:
        return STATE_UP
    if code in TRANSIENT_CODES:
        return None
    return STATE_DOWN


@dataclass(frozen=True)
class Station:
    site_code: str
    rtcm_id: int
    lat: float
    lon: float
    status_code: int
    status_update: str
    distance_km: float = 0.0

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status_code, f"Код {self.status_code}")

    @property
    def status_emoji(self) -> str:
        return STATUS_EMOJI.get(self.status_code, "⚪")

    @property
    def key(self) -> str:
        return self.site_code


def status_label(code: int) -> str:
    return STATUS_LABELS.get(code, f"Код {code}")


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по поверхности Земли (формула haversine), км."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_station(raw: dict[str, Any], settings: Settings) -> Station | None:
    try:
        lat = float(raw["LatDeg"])
        lon = float(raw["LonDeg"])
        return Station(
            site_code=str(raw["SiteCode"]).strip(),
            rtcm_id=int(raw["RtcmId"]),
            lat=lat,
            lon=lon,
            status_code=int(raw["StatusCode"]),
            status_update=str(raw.get("StatusUpdate", "")),
            distance_km=distance_km(settings.center_lat, settings.center_lon, lat, lon),
        )
    except (KeyError, TypeError, ValueError):
        return None


async def fetch_all_stations(settings: Settings, *, max_radius_km: float | None = None) -> list[Station]:
    """Все станции из API (опционально — не дальше max_radius_km от центра), по коду."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(settings.api_url)
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, list):
        raise ValueError("Неожиданный формат ответа API ГЕОСПАЙДЕР")

    stations: list[Station] = []
    for item in payload:
        station = parse_station(item, settings) if isinstance(item, dict) else None
        if station is None:
            continue
        if max_radius_km is not None and station.distance_km > max_radius_km:
            continue
        stations.append(station)

    stations.sort(key=lambda s: s.site_code)
    return stations


async def fetch_stations(settings: Settings) -> list[Station]:
    """Станции в радиусе RADIUS_KM (используется network_check.py)."""
    return await fetch_all_stations(settings, max_radius_km=settings.radius_km)


def format_station_line(station: Station) -> str:
    return (
        f"{station.status_emoji} {station.site_code} "
        f"(RTCM {station.rtcm_id}, {station.distance_km:.0f} км) — {station.status_label}"
    )


def format_status_message(stations: Iterable[Station], title: str) -> str:
    stations = list(stations)
    if not stations:
        return f"{title}\n\nПо вашим настройкам станций не найдено. Измените их в «⚙ настройки»."

    lines = [title, ""]
    working = sum(1 for s in stations if s.status_code in WORKING_CODES)
    lines.append(f"Всего: {len(stations)} · работает: {working}")
    lines.append("")
    lines.extend(format_station_line(s) for s in stations)
    lines.append("")
    lines.append(f"Карта: {MAP_URL}")
    return "\n".join(lines)


def format_change_message(station: Station, new_state: str) -> str:
    if new_state == STATE_UP:
        head = f"🟢 Станция {station.site_code} снова работает"
    else:
        head = f"🔴 Станция {station.site_code} не работает ({station.status_label})"
    return (
        f"{head}\n\n"
        f"RTCM {station.rtcm_id} · {station.distance_km:.0f} км от центра\n"
        f"Обновлено: {station.status_update or '—'}\n"
        f"Координаты: {station.lat:.5f}, {station.lon:.5f}\n\n"
        f"Карта: {MAP_URL}"
    )
