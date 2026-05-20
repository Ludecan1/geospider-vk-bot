"""VK-сообщество: подписка в личку + оповещения о станциях ГЕОСПАЙДЕР."""

from __future__ import annotations

import asyncio
import logging
import sys

from config import Settings, load_settings
from geospider import fetch_stations, format_status_message
from monitor import check_for_changes
from storage import load_subscribers, save_subscribers
from vk_api import VKApiError, VKCommunityClient, run_incoming_loop, unwrap_groups_get_by_id

logger = logging.getLogger(__name__)

SUBSCRIBE = frozenset(
    {"подписка", "start", "начать", "/start", "+", "subscribe", "подписаться"}
)
UNSUBSCRIBE = frozenset(
    {"стоп", "отписаться", "unsubscribe", "-", "/stop", "stop"}
)

TITLE = "Базовые станции ГЕОСПАЙДЕР · 75 км от Великого Новгорода"


def main_menu_keyboard() -> dict:
    """
    Клавиатура под полем ввода в VK (messages.send → keyboard).
    При нажатии VK присылает текст кнопки как обычное сообщение.
    """
    return {
        "one_time": False,
        "inline": False,
        "buttons": [
            [
                {"action": {"type": "text", "label": "подписка"}, "color": "positive"},
                {"action": {"type": "text", "label": "статус"}, "color": "primary"},
            ],
            [
                {"action": {"type": "text", "label": "проверка"}, "color": "primary"},
                {"action": {"type": "text", "label": "помощь"}, "color": "secondary"},
            ],
            [
                {"action": {"type": "text", "label": "стоп"}, "color": "negative"},
            ],
        ],
    }


def _norm(text: str) -> str:
    return text.strip().lower()


async def handle_command(
    settings: Settings, vk: VKCommunityClient, peer_id: int, text: str
) -> None:
    cmd = _norm(text)
    subs = load_subscribers()

    if cmd in SUBSCRIBE or cmd.startswith("начать"):
        subs.add(peer_id)
        save_subscribers(subs)
        await vk.messages_send(
            peer_id,
            "Вы подписаны на уведомления о статусе базовых станций "
            "ГЕОСПАЙДЕР в радиусе 75 км от Великого Новгорода.\n\n"
            "Сейчас пришлю текущий список.\n"
            "Дальше — только при изменении статуса.\n\n"
            "Команды — кнопками ниже или текстом.",
            keyboard=main_menu_keyboard(),
        )
        try:
            stations = await fetch_stations(settings)
            body = format_status_message(
                stations, f"📡 {TITLE}", radius_km=settings.radius_km
            )
            await vk.messages_send(peer_id, body)
        except Exception:
            logger.exception("Ошибка после подписки")
            await vk.messages_send(
                peer_id,
                "Подписка сохранена, но не удалось загрузить данные. Попробуйте «статус» позже.",
            )
        return

    if cmd in UNSUBSCRIBE:
        subs.discard(peer_id)
        save_subscribers(subs)
        await vk.messages_send(
            peer_id,
            "Подписка отключена. Нажмите «подписка» — снова включить.",
            keyboard=main_menu_keyboard(),
        )
        return

    if cmd in ("статус", "/status", "список", "status"):
        try:
            stations = await fetch_stations(settings)
            await vk.messages_send(
                peer_id,
                format_status_message(
                    stations, f"📡 {TITLE}", radius_km=settings.radius_km
                ),
            )
        except Exception as exc:
            await vk.messages_send(peer_id, f"Не удалось загрузить данные: {exc}")
        return

    if cmd in ("проверка", "check", "/check"):
        changes = await check_for_changes(settings)
        if not changes:
            await vk.messages_send(peer_id, "Изменений с прошлой проверки нет.")
        else:
            for part in changes:
                await vk.messages_send(peer_id, part)
        return

    if cmd in ("помощь", "help", "/help", "?"):
        await vk.messages_send(
            peer_id,
            "Команды:\n"
            "• подписка — подписаться и получить список\n"
            "• стоп — отписаться\n"
            "• статус — текущий список станций\n"
            "• проверка — проверить изменения вручную",
            keyboard=main_menu_keyboard(),
        )
        return

    await vk.messages_send(
        peer_id,
        "Не понял команду. Выберите кнопку ниже или напишите «помощь».",
        keyboard=main_menu_keyboard(),
    )


async def poll_loop(settings: Settings, vk: VKCommunityClient) -> None:
    await asyncio.sleep(5)
    while True:
        try:
            subs = load_subscribers()
            if subs:
                changes = await check_for_changes(settings)
                if changes:
                    for msg in changes:
                        for peer_id in list(subs):
                            try:
                                await vk.messages_send(peer_id, msg)
                            except VKApiError as exc:
                                logger.warning("VK send peer=%s: %s", peer_id, exc)
                            except Exception:
                                logger.exception("send peer=%s", peer_id)
                            await asyncio.sleep(0.35)
            else:
                await check_for_changes(settings)
        except Exception:
            logger.exception("Ошибка фонового опроса ГЕОСПАЙДЕР")
        await asyncio.sleep(settings.poll_interval_seconds)


async def verify(settings: Settings, vk: VKCommunityClient) -> None:
    raw = await vk.call("groups.getById", group_ids=str(settings.vk_group_id))
    groups = unwrap_groups_get_by_id(raw)
    if groups:
        logger.info("VK сообщество: %s", groups[0].get("name", "?"))
    stations = await fetch_stations(settings)
    logger.info("ГЕОСПАЙДЕР: %s станций", len(stations))


async def amain(settings: Settings) -> None:
    vk = VKCommunityClient(settings.vk_group_token, settings.vk_group_id)
    try:
        logger.info(
            "VK-бот: входящие через Long Poll; при ошибках 15/27 — опрос messages.getConversations"
        )
        try:
            await verify(settings, vk)
        except VKApiError as exc:
            logger.error("VK: проверка при старте не прошла: %s", exc)
            logger.error(
                "Проверьте VK_GROUP_TOKEN и VK_GROUP_ID в .env (файл рядом с bot.py). "
                "Ключ — «доступ сообщества» с правом «Сообщения сообщества»; при ошибке 27 выпустите новый ключ."
            )
            raise SystemExit(1) from exc
        asyncio.create_task(poll_loop(settings, vk), name="geospider_poll")

        async def on_message(peer_id: int, text: str) -> None:
            if not text:
                return
            await handle_command(settings, vk, peer_id, text)

        await run_incoming_loop(vk, on_message)
    finally:
        await vk.aclose()


def main() -> None:
    from config import DATA_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_file = DATA_DIR / "bot.log"
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    handlers: list[logging.Handler] = [
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    try:
        logging.basicConfig(
            level=logging.INFO,
            format=fmt,
            handlers=handlers,
            force=True,
        )
    except TypeError:
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    try:
        settings = load_settings()
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc

    try:
        asyncio.run(amain(settings))
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")


if __name__ == "__main__":
    main()
