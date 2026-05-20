@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Сначала создайте окружение: дважды щёлкните Запустить_VK_бота.bat
    echo или в этой папке выполните: python -m venv .venv
    echo.
    pause
    exit /b 1
)

echo === Проверка VK и GEOSPIDER ===
echo.
"%~dp0.venv\Scripts\python.exe" "%~dp0network_check.py"
set ERR=%ERRORLEVEL%
echo.
if %ERR% neq 0 (
    echo Код выхода: %ERR%
) else (
    echo Готово.
)
pause
exit /b %ERR%
