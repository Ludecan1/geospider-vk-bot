# Деплой VK-бота ГЕОСПАЙДЕР на [Bothost](https://bothost.ru)

Локальный лаунчер (`Запустить_VK_бота.bat`) на Bothost **не нужен** — там запускается только **`bot.py`** в Docker.

## 1. Репозиторий Git

Bothost тянет код из **GitHub / GitLab** (см. [подключение репозитория](https://bothost.ru/docs/git-repository-access)).

```bash
cd geospider-vk-bot
git init
git add .
git commit -m "VK bot GEOSPIDER for Bothost"
git branch -M main
git remote add origin https://github.com/ВАШ_ЛОГИН/geospider-vk-bot.git
git push -u origin main
```

В репозиторий **не попадают** (уже в `.gitignore`): `.env`, `data/`, `.venv`.

## 2. Создание бота на Bothost

1. Войдите на [bothost.ru/login.php](https://bothost.ru/login.php).
2. [Создать бота](https://bothost.ru/create-bot.php):
   - **Платформа:** VK
   - **Git URL:** ссылка на репозиторий
   - **Ветка:** `main`
   - **Главный файл:** `bot.py` (или автоопределение Python)
3. **Переменные окружения** — из файла `bothost.env.example` (токен и `VK_GROUP_ID` — ваши).

## 3. Данные между перезапусками

Подписчики и состояние станций лежат в **`data/`** (`subscribers.json`, `station_state.json`).

Bothost хранит **`/app/data`** между деплоями ([документация](https://bothost.ru/docs/database-storage)).

- При первом переносе с ПК: через **файловый менеджер** в панели Bothost загрузите папку `data` из локального `geospider-vk-bot/data`.
- Либо заново напишите боту **«подписка»** — подписчики создадутся на сервере.

## 4. После деплоя

- Логи смотрите в панели Bothost (не `launcher.py`).
- Остановите **локальный** экземпляр бота на ПК, иначе два процесса будут дублировать опрос и рассылку.
- Обновление кода: `git push` → Bothost пересоберёт контейнер.

## 5. Проверка

В логах должны быть строки:

- `VK сообщество: …`
- `ГЕОСПАЙДЕР: N станций`
- `Входящие VK: логика lp+fallback-15-27-netpoll-2026-05-v3`

Напишите сообществу **«подписка»** или нажмите кнопку на клавиатуре.
