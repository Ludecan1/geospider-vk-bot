"""Вызовы VK API и Long Poll для сообщений сообщества."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from typing import Any, Callable, Coroutine, Optional

import httpx

logger = logging.getLogger(__name__)

# По этой строке в data/bot.log видно, что подхвачен актуальный vk_api.py (Long Poll → fallback 15/27).
VK_INCOMING_LOGIC_ID = "lp+fallback-15-27-netpoll-2026-05-v3"

VK_API = "https://api.vk.com/method"
API_VERSION = "5.199"
VK_MSG_LIMIT = 4000
# Подряд сетевых сбоев Long Poll (DNS и т.п.) — затем переход на messages.getConversations через api.vk.com.
LONG_POLL_NET_FAILS_BEFORE_POLL_FALLBACK = 2


class VKApiError(RuntimeError):
    """Ошибка ответа VK API; vk_code — числовой error_code из JSON (надёжнее, чем парсить текст)."""

    __slots__ = ("vk_code",)

    def __init__(self, message: str, vk_code: int | None = None) -> None:
        super().__init__(message)
        self.vk_code = vk_code


def vk_error_code(exc: BaseException) -> int | None:
    """Код ошибки VK: из поля vk_code или из начала текста «27 …»."""
    if isinstance(exc, VKApiError):
        if exc.vk_code is not None:
            return exc.vk_code
        m = re.match(r"^\s*(\d+)\b", str(exc))
        if m:
            return int(m.group(1))
    return None


def _long_poll_fallback_codes() -> frozenset[int]:
    """Ошибки groups.getLongPollServer, при которых пробуем messages.getConversations."""
    # 15 — нет права на API управления; 27 — отзыв/несовпадение ключа для Long Poll,
    # но сообщения сообщества часто остаются доступны с тем же ключом.
    return frozenset((15, 27))


def _long_poll_poll_url(server: str) -> str:
    """VK отдаёт поле server как хост или уже с префиксом https://."""
    s = (server or "").strip().rstrip("/")
    if not s:
        raise ValueError("Long Poll: пустое поле server из ответа VK")
    low = s.lower()
    if low.startswith("https://"):
        return s
    if low.startswith("http://"):
        return "https://" + s[7:]
    return f"https://{s}"


def unwrap_groups_get_by_id(response: Any) -> list[dict[str, Any]]:
    """
    groups.getById: в API 5.199+ в response — объект {"groups": [...]},
    в более старых версиях часто приходил сразу список групп.
    """
    if isinstance(response, list):
        return [g for g in response if isinstance(g, dict)]
    if isinstance(response, dict):
        inner = response.get("groups")
        if isinstance(inner, list):
            return [g for g in inner if isinstance(g, dict)]
        if "id" in response or "name" in response:
            return [response]
    return []


class VKCommunityClient:
    def __init__(self, access_token: str, group_id: int) -> None:
        self.access_token = access_token
        self.group_id = group_id
        self._http: Optional[httpx.AsyncClient] = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=60.0)
        return self._http

    async def aclose(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    async def call(self, method: str, **params: Any) -> Any:
        client = await self._client()
        params = {k: v for k, v in params.items() if v is not None}
        params["access_token"] = self.access_token
        params["v"] = API_VERSION
        response = await client.get(f"{VK_API}/{method}", params=params)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            err = data["error"]
            raw_code = err.get("error_code")
            try:
                vk_code = int(raw_code) if raw_code is not None else None
            except (TypeError, ValueError):
                vk_code = None
            msg = err.get("error_msg") or ""
            text = f"{raw_code} {msg}".strip()
            raise VKApiError(text, vk_code=vk_code)
        return data["response"]

    async def messages_send(
        self,
        peer_id: int,
        text: str,
        *,
        keyboard: dict[str, Any] | None = None,
    ) -> None:
        if len(text) > VK_MSG_LIMIT:
            text = text[: VK_MSG_LIMIT - 20] + "\n…(обрезано)"
        params: dict[str, Any] = {
            "peer_id": peer_id,
            "message": text,
            "random_id": random.randint(1, 2_147_000_000),
        }
        if keyboard is not None:
            params["keyboard"] = json.dumps(keyboard, ensure_ascii=False)
        await self.call("messages.send", **params)

    async def get_long_poll_server(self) -> dict[str, Any]:
        return await self.call("groups.getLongPollServer", group_id=self.group_id)

    async def long_poll_check(self, server: str, key: str, ts: str) -> dict[str, Any]:
        client = await self._client()
        url = _long_poll_poll_url(server)
        response = await client.get(
            url,
            params={"act": "a_check", "key": key, "ts": ts, "wait": 25},
        )
        response.raise_for_status()
        return response.json()


def _parse_message_new(update: dict[str, Any]) -> tuple[int, str] | None:
    """Возвращает (peer_id, text) из события message_new."""
    obj = update.get("object")
    if not isinstance(obj, dict):
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        # старый формат: поля на верхнем уровне object
        if "peer_id" in obj or "user_id" in obj:
            msg = obj
        else:
            return None
    if msg.get("out") == 1:
        return None
    peer_id = msg.get("peer_id")
    if peer_id is None and msg.get("user_id") is not None:
        peer_id = msg["user_id"]
    text = (msg.get("text") or msg.get("body") or "").strip()
    if peer_id is None:
        return None
    return int(peer_id), str(text)


async def run_long_poll_loop(
    client: VKCommunityClient,
    on_message: Callable[[int, str], Coroutine[Any, Any, None]],
) -> None:
    """Бесконечный Long Poll; при message_new вызывает on_message(peer_id, text)."""
    logger.info("Входящие VK: логика %s", VK_INCOMING_LOGIC_ID)
    while True:
        try:
            srv = await client.get_long_poll_server()
            server = str(srv["server"])
            key = str(srv["key"])
            ts = str(srv["ts"])
        except VKApiError as exc:
            code = vk_error_code(exc)
            if code in _long_poll_fallback_codes():
                try:
                    await client.call(
                        "messages.getConversations",
                        filter="unread",
                        count=1,
                    )
                except VKApiError as exc2:
                    c2 = vk_error_code(exc2)
                    raise VKApiError(
                        f"Long Poll недоступен (код {code}), "
                        f"messages.getConversations тоже ({c2 or exc2}). "
                        "Перевыпустите ключ в «Управление сообществом» → «Работа с API», "
                        "проверьте VK_GROUP_ID и что ключ от этого же сообщества.",
                        vk_code=c2 if c2 is not None else code,
                    ) from exc2
                logger.warning(
                    "Long Poll недоступен (код %s), переключаюсь на опрос "
                    "messages.getConversations (см. README).",
                    code,
                )
                await run_conversations_poll_loop(client, on_message)
                return
            logger.exception("groups.getLongPollServer")
            await _sleep_retry()
            continue
        except Exception:
            logger.exception("groups.getLongPollServer")
            await _sleep_retry()
            continue

        consecutive_net = 0
        while True:
            try:
                data = await client.long_poll_check(server, key, ts)
                consecutive_net = 0
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "Long Poll HTTP %s для %s — повтор через 5 с.",
                    exc.response.status_code,
                    exc.request.url,
                )
                await asyncio.sleep(5.0)
                continue
            except httpx.RequestError as exc:
                consecutive_net += 1
                logger.warning(
                    "Long Poll сеть (%s): %s — попытка %s/%s",
                    type(exc).__name__,
                    exc,
                    consecutive_net,
                    LONG_POLL_NET_FAILS_BEFORE_POLL_FALLBACK,
                )
                if consecutive_net >= LONG_POLL_NET_FAILS_BEFORE_POLL_FALLBACK:
                    try:
                        await client.call(
                            "messages.getConversations",
                            filter="unread",
                            count=1,
                        )
                    except VKApiError as exc2:
                        logger.warning(
                            "Long Poll по сети не отвечает; messages.getConversations: %s — "
                            "сбрасываю счётчик и продолжаю Long Poll",
                            exc2,
                        )
                        consecutive_net = 0
                    except httpx.RequestError:
                        logger.warning(
                            "Нет сети до Long Poll и до api.vk.com — пауза 20 с."
                        )
                        await asyncio.sleep(20.0)
                        consecutive_net = 0
                    else:
                        logger.warning(
                            "Long Poll недоступен из‑за сети — переключаюсь на опрос "
                            "messages.getConversations (тот же api.vk.com, отклик ~2–3 с)."
                        )
                        await run_conversations_poll_loop(client, on_message)
                        return
                await asyncio.sleep(5.0)
                continue
            except Exception:
                logger.exception("Long Poll request")
                break

            if data.get("failed") == 1:
                logger.warning("Long Poll: key устарел, перезапрос сервера")
                break
            if data.get("failed") == 2:
                ts = str(data.get("ts", ts))
                continue

            ts = str(data.get("ts", ts))
            for update in data.get("updates") or []:
                if not isinstance(update, dict):
                    continue
                if update.get("type") != "message_new":
                    continue
                parsed = _parse_message_new(update)
                if parsed:
                    peer_id, text = parsed
                    try:
                        await on_message(peer_id, text)
                    except Exception:
                        logger.exception("on_message peer_id=%s", peer_id)


async def _sleep_retry() -> None:
    await asyncio.sleep(5)


async def run_conversations_poll_loop(
    client: VKCommunityClient,
    on_message: Callable[[int, str], Coroutine[Any, Any, None]],
) -> None:
    """
    Запасной приём входящих, если groups.getLongPollServer недоступен (часто 15 или 27).
    Опрасывает непрочитанные диалоги; реакция на команды с задержкой ~2–3 с.
    """
    logger.info(
        "Режим опроса messages.getConversations (ключ без Long Poll API). "
        "Чтобы использовать Long Poll, создайте ключ с доступом к API управления сообществом."
    )
    processed: set[tuple[int, int]] = set()
    first_clear = True

    while True:
        try:
            data = await client.call(
                "messages.getConversations",
                filter="unread",
                count=40,
                extended=0,
            )
        except VKApiError:
            logger.exception("messages.getConversations")
            await asyncio.sleep(8)
            continue
        except Exception:
            logger.exception("messages.getConversations")
            await asyncio.sleep(8)
            continue

        items = data.get("items") or []

        if first_clear:
            for item in items:
                lm = item.get("last_message")
                if isinstance(lm, dict) and lm.get("peer_id") is not None:
                    try:
                        await client.call("messages.markAsRead", peer_id=lm["peer_id"])
                    except Exception:
                        pass
            first_clear = False
            if items:
                logger.info(
                    "Старые непрочитанные помечены прочитанными. "
                    "Если нужна подписка — отправьте «подписка» ещё раз."
                )
            await asyncio.sleep(1.0)
            continue

        for item in items:
            lm = item.get("last_message")
            if not isinstance(lm, dict):
                continue
            if lm.get("out") == 1:
                continue
            peer_id = lm.get("peer_id")
            cmid = lm.get("conversation_message_id")
            if cmid is None:
                cmid = lm.get("id")
            if peer_id is None or cmid is None:
                continue
            key = (int(peer_id), int(cmid))
            if key in processed:
                continue
            processed.add(key)
            if len(processed) > 10000:
                processed.clear()

            text = (lm.get("text") or "").strip()
            if text:
                try:
                    await on_message(int(peer_id), text)
                except Exception:
                    logger.exception("on_message peer_id=%s", peer_id)
            try:
                await client.call("messages.markAsRead", peer_id=peer_id)
            except Exception:
                pass

        await asyncio.sleep(2.5)


async def run_incoming_loop(
    client: VKCommunityClient,
    on_message: Callable[[int, str], Coroutine[Any, Any, None]],
) -> None:
    """
    Long Poll; при 15/27 на getLongPollServer — опрос диалогов.
    При VK_INCOMING_POLL_ONLY=1 — сразу только опрос (если Long Poll-хост недоступен по DNS/VPN).
    """
    flag = (os.getenv("VK_INCOMING_POLL_ONLY") or "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        logger.warning(
            "VK_INCOMING_POLL_ONLY: входящие только через messages.getConversations (без Long Poll)"
        )
        await run_conversations_poll_loop(client, on_message)
        return
    await run_long_poll_loop(client, on_message)
