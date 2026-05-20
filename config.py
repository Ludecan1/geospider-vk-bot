from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Всегда .env рядом с этим файлом (не «текущая папка»), и с override=True — иначе старый
# VK_GROUP_TOKEN из переменных среды Windows перекрывает новый ключ в файле.
BOT_ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=BOT_ENV_FILE, override=True)

DATA_DIR = Path(__file__).resolve().parent / "data"
STATE_FILE = DATA_DIR / "station_state.json"
SUBSCRIBERS_FILE = DATA_DIR / "subscribers.json"


@dataclass(frozen=True)
class Settings:
    vk_group_token: str
    vk_group_id: int
    poll_interval_seconds: int
    api_url: str
    center_lat: float
    center_lon: float
    radius_km: float


def _clean_vk_token(raw: str) -> str:
    s = raw.strip().strip("\ufeff").strip("\u200b")
    if len(s) >= 2 and ((s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
        s = s[1:-1].strip()
    s = re.sub(r"\s+", "", s)
    return s


def load_settings() -> Settings:
    from env_utils import ENV_FILE, count_env_assignments, read_env_value

    if ENV_FILE.exists():
        if count_env_assignments("VK_GROUP_TOKEN") > 1:
            logger.warning(
                "В .env несколько строк VK_GROUP_TOKEN= — используется последняя непустая; "
                "оставьте одну строку, чтобы не путаться."
            )
        if count_env_assignments("VK_GROUP_ID") > 1:
            logger.warning(
                "В .env несколько строк VK_GROUP_ID= — используется последняя непустая."
            )

    token = read_env_value("VK_GROUP_TOKEN") or os.getenv("VK_GROUP_TOKEN", "")
    token = _clean_vk_token(token)
    if not token:
        raise RuntimeError(
            "Задайте VK_GROUP_TOKEN в .env — ключ доступа сообщества с правом «Сообщения сообщества»"
        )
    gid_raw = read_env_value("VK_GROUP_ID") or os.getenv("VK_GROUP_ID", "")
    gid = gid_raw.strip()
    if not gid:
        raise RuntimeError(
            "Задайте VK_GROUP_ID в .env — числовой id группы (без club/public, только цифры)"
        )
    # VK в URL обычно ожидает положительный числовой id (club123 → 123).
    try:
        gid_int = abs(int(gid.lstrip("+")))
    except ValueError as exc:
        raise RuntimeError(
            "VK_GROUP_ID в .env должен быть числом (например 238890367 для club238890367)"
        ) from exc

    return Settings(
        vk_group_token=token,
        vk_group_id=gid_int,
        poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "60")),
        api_url=os.getenv(
            "GEOSPIDER_API_URL",
            "https://api.geospider.ru/geospider/SitesInfo.asmx/GetAllSites",
        ),
        center_lat=float(os.getenv("CENTER_LAT", "58.5213")),
        center_lon=float(os.getenv("CENTER_LON", "31.2755")),
        radius_km=float(os.getenv("RADIUS_KM", "75")),
    )
