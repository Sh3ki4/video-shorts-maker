@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist "VideoShortsMaker\VideoShortsMaker.exe" (
  start "" "VideoShortsMaker\VideoShortsMaker.exe"
  exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
  where py >nul 2>nul && py -3 -m venv .venv
)
if not exist ".venv\Scripts\python.exe" (
  where python >nul 2>nul && python -m venv .venv
)
if not exist ".venv\Scripts\python.exe" goto :python_error

if not exist ".venv\.base_ready" (
  echo Первый запуск: устанавливается графический интерфейс...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto :python_error
  type nul > ".venv\.base_ready"
)
start "" ".venv\Scripts\pythonw.exe" "app.py"
exit /b 0

:python_error
echo.
echo Не найден рабочий Python 3.10–3.12.
echo Установите Python с https://www.python.org/downloads/windows/
echo При установке отметьте "Add Python to PATH", затем снова запустите этот файл.
echo.
pause
exit /b 1
