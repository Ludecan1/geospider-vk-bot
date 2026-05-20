@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
    ) else (
        echo Нет .env и .env.example
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo Создаю виртуальное окружение...
    python -m venv .venv
    if errorlevel 1 (
        echo Не удалось: установите Python и добавьте в PATH
        pause
        exit /b 1
    )
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip -q
    pip install -r requirements.txt -q
    if errorlevel 1 (
        echo Ошибка pip install
        pause
        exit /b 1
    )
)

REM Окно лаунчера висит, как у Telegram-бота (python.exe, не pythonw)
start "" "%~dp0.venv\Scripts\python.exe" "%~dp0launcher.py"
exit /b 0
