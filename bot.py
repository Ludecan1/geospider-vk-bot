"""VK-сообщество: подписка в личку + оповещения о станциях ГЕОСПАЙДЕР."""

from __future__ import annotations

import asyncio
import logging
import re
import sys

from config import Settings, load_settings
from geospider import (
    STATE_DOWN,
    STATE_UP,
    Station,
    format_change_message,
    format_status_message,
)
from monitor import StationEvent, check_for_changes, get_stations
from storage import (
    EVENTS_BOTH,
    EVENTS_DOWN,
    EVENTS_UP,
    UserSettings,
    ensure_subscribers_seeded,
    load_subscribers,
    load_user_settings,
    save_subscribers,
    save_user_settings,
)
from vk_api import (
    VKApiError,
    VKCommunityClient,
    run_incoming_loop,
    unwrap_groups_get_by_id,
    vk_error_code,
)

logger = logging.getLogger(__name__)

SUBSCRIBE = frozenset({"подписка", "start", "начать", "subscribe", "подписаться"})
UNSUBSCRIBE = frozenset({"стоп", "отписаться", "unsubscribe", "stop"})

TITLE = "Базовые станции ГЕОСПАЙДЕР"

EVENT_LABELS = {
    EVENTS_BOTH: "работает и не работает",
    EVENTS_DOWN: "только «не работает»",
    EVENTS_UP: "только «снова работает»",
}

RADIUS_CHOICES = (10, 25, 50, 75, 100, 150, 200, 300)
RADIUS_MIN, RADIUS_MAX = 1, 1000
STATIONS_PER_PAGE = 16  # 8 рядов по 2 кнопки + навигация (лимит VK — 10 рядов)

# Текущее меню и страница списка станций у каждого собеседника (в памяти процесса).
_menu: dict[int, str] = {}
_page: dict[int, int] = {}


# ---------------------------------------------------------------- клавиатуры


def _btn(label: str, color: str = "secondary") -> dict:
    return {"action": {"type": "text", "label": label[:40]}, "color": color}


def _kb(rows: list[list[dict]]) -> dict:
    return {"one_time": False, "inline": False, "buttons": rows}


def main_menu_keyboard() -> dict:
    return _kb(
        [
            [_btn("подписка", "positive"), _btn("статус", "primary")],
            [_btn("проверка", "primary"), _btn("⚙ настройки", "primary")],
            [_btn("помощь"), _btn("стоп", "negative")],
        ]
    )


def settings_keyboard() -> dict:
    return _kb(
        [
            [_btn("📏 радиус", "primary"), _btn("📡 станции", "primary")],
            [_btn("🔔 уведомления", "primary")],
            [_btn("⬅ назад")],
        ]
    )


def radius_keyboard(current: float) -> dict:
    rows: list[list[dict]] = []
    row: list[dict] = []
    for r in RADIUS_CHOICES:
        row.append(_btn(f"{r} км", "positive" if r == current else "secondary"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([_btn("⬅ назад")])
    return _kb(rows)


def events_keyboard(current: str) -> dict:
    def b(mode: str, label: str) -> dict:
        return _btn(label, "positive" if mode == current else "secondary")

    return _kb(
        [
            [b(EVENTS_BOTH, "все события")],
            [b(EVENTS_DOWN, "только отключения")],
            [b(EVENTS_UP, "только восстановления")],
            [_btn("⬅ назад")],
        ]
    )


def stations_keyboard(page_items: list[Station], us: UserSettings, page: int, pages: int) -> dict:
    rows: list[list[dict]] = []
    for i in range(0, len(page_items), 2):
        rows.append(
            [
                _btn(
                    f"{'✅' if _is_selected(us, s) else '⬜'} {s.site_code}",
                    "positive" if _is_selected(us, s) else "secondary",
                )
                for s in page_items[i : i + 2]
            ]
        )
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(_btn("◀ пред."))
        if page < pages - 1:
            nav.append(_btn("след. ▶"))
        rows.append(nav)
    rows.append([_btn("все станции", "primary"), _btn("снять все"), _btn("✔ готово", "positive")])
    return _kb(rows)


# ---------------------------------------------------------------- фильтры


def _is_selected(us: UserSettings, station: Station) -> bool:
    """Будут ли уведомления по станции с этими настройками."""
    if us.stations is not None:
        return station.site_code in us.stations
    return station.distance_km <= us.radius_km


def _wants_event(us: UserSettings, ev: StationEvent) -> bool:
    if us.events == EVENTS_DOWN and ev.new_state != STATE_DOWN:
        return False
    if us.events == EVENTS_UP and ev.new_state != STATE_UP:
        return False
    return _is_selected(us, ev.station)


def _selector_list(stations: list[Station], us: UserSettings) -> list[Station]:
    """Станции для меню выбора: в радиусе пользователя + уже выбранные вручную."""
    chosen = us.stations or set()
    items = [s for s in stations if s.distance_km <= us.radius_km or s.site_code in chosen]
    items.sort(key=lambda s: (s.distance_km, s.site_code))
    return items


def _fmt_km(v: float) -> str:
    return f"{v:g}"


# ---------------------------------------------------------------- экраны


def _settings_text(us: UserSettings, stations: list[Station], interval: int) -> str:
    if us.stations is None:
        n = sum(1 for s in stations if s.distance_km <= us.radius_km)
        st = f"все в радиусе ({n} шт.)"
    elif not us.stations:
        st = "не выбрано ни одной — уведомлений не будет"
    else:
        codes = sorted(us.stations)
        shown = ", ".join(codes[:15]) + (" …" if len(codes) > 15 else "")
        st = f"выбрано {len(codes)}: {shown}"
    return (
        "⚙ Настройки уведомлений\n\n"
        f"📏 Радиус: {_fmt_km(us.radius_km)} км\n"
        f"📡 Станции: {st}\n"
        f"🔔 Уведомлять: {EVENT_LABELS[us.events]}\n\n"
        f"Сайт опрашивается каждые {interval} с. Статус «Соединяется» уведомлений не вызывает.\n"
        "Выберите, что изменить, кнопками ниже."
    )


async def show_main(vk: VKCommunityClient, peer_id: int, text: str) -> None:
    _menu[peer_id] = "main"
    await vk.messages_send(peer_id, text, keyboard=main_menu_keyboard())


async def show_settings(settings: Settings, vk: VKCommunityClient, peer_id: int, prefix: str = "") -> None:
    _menu[peer_id] = "settings"
    us = load_user_settings(peer_id)
    stations = await get_stations(settings)
    text = _settings_text(us, stations, settings.poll_interval_seconds)
    await vk.messages_send(peer_id, f"{prefix}\n\n{text}" if prefix else text, keyboard=settings_keyboard())


async def show_radius(vk: VKCommunityClient, peer_id: int, prefix: str = "") -> None:
    _menu[peer_id] = "radius"
    us = load_user_settings(peer_id)
    text = (
        f"📏 Текущий радиус: {_fmt_km(us.radius_km)} км от центра Великого Новгорода.\n\n"
        f"Выберите кнопкой или напишите число, например «60» ({RADIUS_MIN}–{RADIUS_MAX} км)."
    )
    await vk.messages_send(
        peer_id, f"{prefix}\n\n{text}" if prefix else text, keyboard=radius_keyboard(us.radius_km)
    )


async def show_events(vk: VKCommunityClient, peer_id: int, prefix: str = "") -> None:
    _menu[peer_id] = "events"
    us = load_user_settings(peer_id)
    text = (
        f"🔔 Сейчас: {EVENT_LABELS[us.events]}.\n\n"
        "• все события — когда станция перестала работать и когда снова заработала\n"
        "• только отключения — только «не работает»\n"
        "• только восстановления — только «снова работает»"
    )
    await vk.messages_send(
        peer_id, f"{prefix}\n\n{text}" if prefix else text, keyboard=events_keyboard(us.events)
    )


async def show_stations(
    settings: Settings, vk: VKCommunityClient, peer_id: int, prefix: str = "", page: int | None = None
) -> None:
    _menu[peer_id] = "stations"
    us = load_user_settings(peer_id)
    items = _selector_list(await get_stations(settings), us)
    if not items:
        await vk.messages_send(
            peer_id,
            f"В радиусе {_fmt_km(us.radius_km)} км станций нет. Увеличьте радиус.",
            keyboard=settings_keyboard(),
        )
        _menu[peer_id] = "settings"
        return

    pages = (len(items) + STATIONS_PER_PAGE - 1) // STATIONS_PER_PAGE
    page = _page.get(peer_id, 0) if page is None else page
    page = max(0, min(page, pages - 1))
    _page[peer_id] = page
    page_items = items[page * STATIONS_PER_PAGE : (page + 1) * STATIONS_PER_PAGE]

    mode = (
        "Сейчас: все станции в радиусе."
        if us.stations is None
        else f"Сейчас: выбрано вручную — {len(us.stations)} шт."
    )
    lines = [
        f"📡 Выбор станций (стр. {page + 1}/{pages}). {mode}",
        "Нажмите на станцию, чтобы включить ✅ или выключить ⬜ уведомления по ней.",
        "",
    ]
    for s in page_items:
        mark = "✅" if _is_selected(us, s) else "⬜"
        lines.append(f"{mark} {s.site_code} — {s.distance_km:.0f} км, {s.status_label}")
    lines += ["", "«все станции» — снова следить за всеми в радиусе."]
    text = "\n".join(lines)
    await vk.messages_send(
        peer_id,
        f"{prefix}\n\n{text}" if prefix else text,
        keyboard=stations_keyboard(page_items, us, page, pages),
    )


# ---------------------------------------------------------------- команды


def _core(text: str) -> str:
    """Текст без эмодзи/знаков по краям: «⚙ настройки» → «настройки», «✅ NVGR» → «nvgr»."""
    return re.sub(r"^[\W_]+|[\W_]+$", "", text.strip().lower())


async def _toggle_station(settings: Settings, vk: VKCommunityClient, peer_id: int, code: str) -> None:
    us = load_user_settings(peer_id)
    stations = await get_stations(settings)
    if us.stations is None:
        # Переход из режима «все в радиусе» в ручной выбор: стартуем со всех в радиусе.
        us.stations = {s.site_code for s in stations if s.distance_km <= us.radius_km}
    if code in us.stations:
        us.stations.discard(code)
        note = f"⬜ {code}: уведомления выключены."
    else:
        us.stations.add(code)
        note = f"✅ {code}: уведомления включены."
    save_user_settings(peer_id, us)
    await show_stations(settings, vk, peer_id, prefix=note)


async def _set_radius(settings: Settings, vk: VKCommunityClient, peer_id: int, value: float) -> None:
    if not (RADIUS_MIN <= value <= RADIUS_MAX):
        await show_radius(vk, peer_id, prefix=f"Радиус должен быть от {RADIUS_MIN} до {RADIUS_MAX} км.")
        return
    us = load_user_settings(peer_id)
    us.radius_km = value
    save_user_settings(peer_id, us)
    note = f"✅ Радиус: {_fmt_km(value)} км."
    if us.stations is not None:
        note += (
            "\nСейчас выбраны конкретные станции — радиус влияет только на список выбора. "
            "Чтобы следить за всеми в радиусе, нажмите «📡 станции» → «все станции»."
        )
    await show_settings(settings, vk, peer_id, prefix=note)


async def handle_command(
    settings: Settings, vk: VKCommunityClient, peer_id: int, text: str
) -> None:
    raw = text.strip().lower()
    cmd = _core(text)
    menu = _menu.get(peer_id, "main")

    # --- подписка / отписка
    if raw in ("/start", "+") or cmd in SUBSCRIBE or cmd.startswith("начать"):
        subs = load_subscribers()
        subs.add(peer_id)
        save_subscribers(subs)
        await show_main(
            vk,
            peer_id,
            "Вы подписаны на уведомления о базовых станциях ГЕОСПАЙДЕР.\n\n"
            "Уведомление придёт, только когда станция перестала работать "
            "или снова заработала (промежуточное «Соединяется» не присылаю).\n\n"
            "Радиус, станции и тип уведомлений — кнопка «⚙ настройки».\n"
            "Сейчас пришлю текущий список.",
        )
        await _send_status(settings, vk, peer_id)
        return

    if raw in ("-", "/stop") or cmd in UNSUBSCRIBE:
        subs = load_subscribers()
        subs.discard(peer_id)
        save_subscribers(subs)
        await show_main(vk, peer_id, "Подписка отключена. Нажмите «подписка» — снова включить.")
        return

    # --- основное меню
    if cmd in ("статус", "status", "список"):
        await _send_status(settings, vk, peer_id)
        return

    if cmd in ("проверка", "check"):
        events = await check_for_changes(settings)
        sent_to_me = await broadcast(vk, events)
        if peer_id not in sent_to_me:
            await vk.messages_send(peer_id, "Изменений «работает / не работает» по вашим станциям нет.")
        return

    if cmd in ("помощь", "help") or raw == "?":
        await show_main(
            vk,
            peer_id,
            "Команды:\n"
            "• подписка — подписаться и получить список\n"
            "• стоп — отписаться\n"
            "• статус — текущий список ваших станций\n"
            "• проверка — проверить изменения прямо сейчас\n"
            "• ⚙ настройки — радиус, выбор станций, тип уведомлений\n\n"
            "Уведомления приходят только при переходе «работает ⇄ не работает».",
        )
        return

    # --- настройки
    if cmd in ("настройки", "settings", "готово"):
        await show_settings(settings, vk, peer_id)
        return

    if cmd == "назад":
        if menu in ("radius", "stations", "events"):
            await show_settings(settings, vk, peer_id)
        else:
            await show_main(vk, peer_id, "Главное меню.")
        return

    if cmd == "радиус":
        await show_radius(vk, peer_id)
        return

    m = re.fullmatch(r"(?:радиус\s*)?(\d+(?:[.,]\d+)?)\s*(км)?", cmd)
    if m and (menu == "radius" or m.group(2) or cmd.startswith("радиус")):
        await _set_radius(settings, vk, peer_id, float(m.group(1).replace(",", ".")))
        return

    if cmd in ("уведомления", "события"):
        await show_events(vk, peer_id)
        return

    event_modes = {
        "все события": EVENTS_BOTH,
        "только отключения": EVENTS_DOWN,
        "только восстановления": EVENTS_UP,
    }
    if cmd in event_modes:
        us = load_user_settings(peer_id)
        us.events = event_modes[cmd]
        save_user_settings(peer_id, us)
        await show_settings(settings, vk, peer_id, prefix=f"✅ Уведомлять: {EVENT_LABELS[us.events]}.")
        return

    if cmd == "станции":
        await show_stations(settings, vk, peer_id, page=0)
        return

    if cmd == "все станции":
        us = load_user_settings(peer_id)
        us.stations = None
        save_user_settings(peer_id, us)
        await show_stations(settings, vk, peer_id, prefix="✅ Слежу за всеми станциями в радиусе.")
        return

    if cmd == "снять все":
        us = load_user_settings(peer_id)
        us.stations = set()
        save_user_settings(peer_id, us)
        await show_stations(settings, vk, peer_id, prefix="Все станции сняты — отметьте нужные.")
        return

    if cmd in ("пред", "след"):
        delta = -1 if cmd == "пред" else 1
        await show_stations(settings, vk, peer_id, page=_page.get(peer_id, 0) + delta)
        return

    # Нажатие на станцию («✅ CODE» / «⬜ CODE») или просто код станции текстом.
    if cmd:
        by_code = {s.site_code.lower(): s.site_code for s in await get_stations(settings)}
        if cmd in by_code:
            await _toggle_station(settings, vk, peer_id, by_code[cmd])
            return

    await show_main(vk, peer_id, "Не понял команду. Выберите кнопку ниже или напишите «помощь».")


async def _send_status(settings: Settings, vk: VKCommunityClient, peer_id: int) -> None:
    us = load_user_settings(peer_id)
    try:
        stations = await get_stations(settings)
    except Exception as exc:
        logger.exception("Загрузка станций для статуса")
        await vk.messages_send(peer_id, f"Не удалось загрузить данные: {exc}")
        return
    mine = sorted((s for s in stations if _is_selected(us, s)), key=lambda s: s.site_code)
    if us.stations is None:
        title = f"📡 {TITLE} · {_fmt_km(us.radius_km)} км от Великого Новгорода"
    else:
        title = f"📡 {TITLE} · выбранные станции"
    await vk.messages_send(peer_id, format_status_message(mine, title))


# ---------------------------------------------------------------- рассылка


async def broadcast(vk: VKCommunityClient, events: list[StationEvent]) -> set[int]:
    """Рассылает события подписчикам по их настройкам. Возвращает, кому что-то ушло."""
    delivered: set[int] = set()
    if not events:
        return delivered
    for peer_id in sorted(load_subscribers()):
        us = load_user_settings(peer_id)
        for ev in events:
            if not _wants_event(us, ev):
                continue
            try:
                await vk.messages_send(peer_id, format_change_message(ev.station, ev.new_state))
                delivered.add(peer_id)
                logger.info(
                    "Оповещение отправлено peer=%s %s→%s", peer_id, ev.station.site_code, ev.new_state
                )
            except VKApiError as exc:
                logger.warning("VK send peer=%s: %s", peer_id, exc)
            except Exception:
                logger.exception("send peer=%s", peer_id)
            await asyncio.sleep(0.35)
    return delivered


async def poll_loop(settings: Settings, vk: VKCommunityClient) -> None:
    await asyncio.sleep(5)
    while True:
        try:
            events = await check_for_changes(settings)
            await broadcast(vk, events)
        except Exception:
            logger.exception("Ошибка фонового опроса ГЕОСПАЙДЕР")
        await asyncio.sleep(settings.poll_interval_seconds)


async def verify(settings: Settings, vk: VKCommunityClient) -> None:
    raw = await vk.call("groups.getById", group_ids=str(settings.vk_group_id))
    groups = unwrap_groups_get_by_id(raw)
    if groups:
        logger.info("VK сообщество: %s", groups[0].get("name", "?"))
    logger.info("VK-токен: %s символов", len(settings.vk_group_token))
    try:
        await vk.get_long_poll_server()
    except VKApiError as exc:
        code = vk_error_code(exc)
        if code in (15, 27):
            await vk.call("messages.getConversations", filter="unread", count=1)
            logger.info("VK messages API: OK (Long Poll код %s)", code)
        else:
            raise
    else:
        logger.info("VK Long Poll: OK")
    n_subs = ensure_subscribers_seeded()
    logger.info("Подписчиков на оповещения: %s", n_subs)
    stations = await get_stations(settings)
    near = sum(1 for s in stations if s.distance_km <= settings.radius_km)
    logger.info(
        "ГЕОСПАЙДЕР: %s станций всего, %s в радиусе %s км; опрос каждые %s с",
        len(stations),
        near,
        _fmt_km(settings.radius_km),
        settings.poll_interval_seconds,
    )


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
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers, force=True)
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
