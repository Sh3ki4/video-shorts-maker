@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call :ensure_python || exit /b 1
echo Устанавливается локальный Whisper. Это может занять несколько минут...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements-whisper.txt
if errorlevel 1 goto :error
echo.
echo Whisper установлен. При первом распознавании модель загрузится автоматически.
pause
exit /b 0

:ensure_python
if exist ".venv\Scripts\python.exe" exit /b 0
where py >nul 2>nul && (py -3 -m venv .venv & exit /b %errorlevel%)
where python >nul 2>nul && (python -m venv .venv & exit /b %errorlevel%)
echo Python не найден. Сначала установите Python 3.10–3.12.
pause
exit /b 1

:error
echo.
echo Установка не завершилась. Проверьте интернет и свободное место.
pause
exit /b 1
