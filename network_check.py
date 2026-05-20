"""Проверка VK API и ГЕОСПАЙДЕР перед запуском."""

from __future__ import annotations

import asyncio
import logging
import sys

from config import BOT_ENV_FILE, load_settings
from geospider import fetch_stations
from vk_api import (
    VKApiError,
    VKCommunityClient,
    unwrap_groups_get_by_id,
    vk_error_code,
)


async def main() -> int:
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if BOT_ENV_FILE.exists():
        print(f"  OK  Файл настроек: {BOT_ENV_FILE.resolve()}")
    else:
        print("  WARN: .env рядом с ботом не найден — токен только из переменных среды")

    try:
        settings = load_settings()
    except RuntimeError as exc:
        print(f"Ошибка настроек: {exc}")
        return 1

    vk = VKCommunityClient(settings.vk_group_token, settings.vk_group_id)
    try:
        try:
            raw = await vk.call("groups.getById", group_ids=str(settings.vk_group_id))
        except VKApiError as exc:
            code = vk_error_code(exc)
            print(f"  FAIL: groups.getById — {exc}")
            if code == 27:
                print(
                    "  Подсказка: ключ отозван или выдан не для этого сообщества. "
                    "В vk.com откройте сообщество → Управление → Работа с API → "
                    "создайте новый ключ с правом «Сообщения сообщества», "
                    "вставьте в .env и проверьте, что VK_GROUP_ID — id именно этой группы."
                )
            return 1

        groups = unwrap_groups_get_by_id(raw)
        if not groups:
            print(f"  FAIL: groups.getById — пустой или неожиданный ответ ({type(raw).__name__})")
            return 1
        g0 = groups[0]
        name = g0.get("name", "?")
        api_id = g0.get("id")
        print(f"  OK  VK сообщество: {name}")
        if api_id is not None:
            try:
                id_mismatch = abs(int(api_id)) != settings.vk_group_id
            except (TypeError, ValueError):
                id_mismatch = False
        else:
            id_mismatch = False
        if id_mismatch:
            print(
                f"  WARN: в .env VK_GROUP_ID={settings.vk_group_id}, "
                f"а у ответа VK id={api_id}. Лучше выровнять id в .env под ответ API."
            )

        try:
            srv = await vk.get_long_poll_server()
            print(f"  OK  Long Poll: {srv.get('server', '?')}")
        except VKApiError as exc:
            code = vk_error_code(exc)
            if code in (15, 27):
                try:
                    await vk.call("messages.getConversations", filter="unread", count=1)
                except VKApiError as exc2:
                    print(f"  FAIL: messages.getConversations — {exc2}")
                    print(
                        "  Подсказка: и Long Poll, и диалоги недоступны с этим ключом. "
                        "Перевыпустите ключ сообщества (см. README)."
                    )
                    return 1
                print(
                    "  OK  Входящие: режим опроса диалогов "
                    f"(Long Poll недоступен, код {code} — см. README)"
                )
            else:
                print(f"  FAIL: groups.getLongPollServer — {exc}")
                return 1

        stations = await fetch_stations(settings)
        print(
            f"  OK  GEOSPIDER API — станций в радиусе {settings.radius_km:g} км: {len(stations)}"
        )
    except Exception as exc:
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        return 1
    finally:
        await vk.aclose()

    print("\nГотово. Запуск: python bot.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
