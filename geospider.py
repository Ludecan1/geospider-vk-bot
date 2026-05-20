"""Опрос API ГЕОСПАЙДЕР и форматирование (текст для VK, без HTML)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

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


@dataclass(frozen=True)
class Station:
    site_code: str
    rtcm_id: int
    lat: float
    lon: float
    status_code: int
    status_update: str

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status_code, f"Код {self.status_code}")

    @property
    def status_emoji(self) -> str:
        return STATUS_EMOJI.get(self.status_code, "⚪")

    @property
    def key(self) -> str:
        return self.site_code

    def to_state_value(self) -> dict[str, Any]:
        return {
            "status_code": self.status_code,
            "status_update": self.status_update,
        }


def status_label(code: int) -> str:
    return STATUS_LABELS.get(code, f"Код {code}")


def parse_station(raw: dict[str, Any]) -> Station | None:
    try:
        lat = float(raw["LatDeg"])
        lon = float(raw["LonDeg"])
        return Station(
            site_code=str(raw["SiteCode"]),
            rtcm_id=int(raw["RtcmId"]),
            lat=lat,
            lon=lon,
            status_code=int(raw["StatusCode"]),
            status_update=str(raw.get("StatusUpdate", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние по поверхности Земли (формула haversine), км."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def in_region(station: Station, settings: Settings) -> bool:
    return (
        distance_km(settings.center_lat, settings.center_lon, station.lat, station.lon)
        <= settings.radius_km
    )


async def fetch_stations(settings: Settings) -> list[Station]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(settings.api_url)
        response.raise_for_status()
        payload = response.json()

    if not isinstance(payload, list):
        raise ValueError("Неожиданный формат ответа API ГЕОСПАЙДЕР")

    stations: list[Station] = []
    for item in payload:
        station = parse_station(item)
        if station and in_region(station, settings):
            stations.append(station)

    stations.sort(key=lambda s: s.site_code)
    return stations


def format_station_line(station: Station) -> str:
    return (
        f"{station.status_emoji} {station.site_code} "
        f"(RTCM {station.rtcm_id}) — {station.status_label}"
    )


def format_status_message(stations: list[Station], title: str, *, radius_km: float = 75) -> str:
    if not stations:
        return f"{title}\n\nВ радиусе {radius_km:g} км от центра станций не найдено."

    lines = [title, ""]
    working = sum(1 for s in stations if s.status_code == 3)
    lines.append(f"Всего: {len(stations)} · работает: {working}")
    lines.append("")
    lines.extend(format_station_line(s) for s in stations)
    lines.append("")
    lines.append(f"Карта: {MAP_URL}")
    return "\n".join(lines)


def format_change_message(
    station: Station, old_code: int, old_update: str
) -> str:
    old_l = status_label(old_code)
    return (
        "⚠️ Изменение статуса\n\n"
        f"{station.site_code} (RTCM {station.rtcm_id})\n"
        f"Было: {old_l}\n"
        f"Стало: {station.status_label}\n"
        f"Обновлено: {station.status_update or '—'}\n"
        f"Координаты: {station.lat:.5f}, {station.lon:.5f}\n\n"
        f"Карта: {MAP_URL}"
    )
