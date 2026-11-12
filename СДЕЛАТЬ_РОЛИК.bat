@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Перетащи на этот файл задачу .md (можно вместе с видео-референсом)
if "%~1"=="" (
  echo Перетащи файл задачи .md на СДЕЛАТЬ_РОЛИК.bat
  echo   или: СДЕЛАТЬ_РОЛИК.bat задача.md [референс.mp4] [--channel "Канал"]
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Первый запуск: ставлю зависимости...
  python -m venv .venv || (echo Нужен Python 3.10+ & pause & exit /b 1)
  ".venv\Scripts\python" -m pip install -q -r requirements.txt
)
".venv\Scripts\python" -m app.cli %*
echo.
pause
