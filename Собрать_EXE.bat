@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv-build\Scripts\python.exe" (
  where py >nul 2>nul && py -3 -m venv .venv-build
)
if not exist ".venv-build\Scripts\python.exe" (
  where python >nul 2>nul && python -m venv .venv-build
)
if not exist ".venv-build\Scripts\python.exe" goto :error
".venv-build\Scripts\python.exe" -m pip install --upgrade pip pyinstaller -r requirements.txt -r requirements-whisper.txt -r requirements-groq.txt
if errorlevel 1 goto :error
".venv-build\Scripts\pyinstaller.exe" --noconfirm --clean --onedir --windowed --name VideoShortsMaker --collect-all faster_whisper --collect-all ctranslate2 --collect-all tokenizers --collect-all huggingface_hub --collect-all groq --collect-all av app.py
if errorlevel 1 goto :error
copy /y "Установить_FFmpeg.bat" "dist\VideoShortsMaker\" >nul
copy /y "scripts_install_ffmpeg.ps1" "dist\VideoShortsMaker\" >nul
if exist "tools\ffmpeg\bin\ffmpeg.exe" (
  xcopy /e /i /y "tools\ffmpeg\bin" "dist\VideoShortsMaker\tools\ffmpeg\bin" >nul
)
echo.
echo Готово: dist\VideoShortsMaker\VideoShortsMaker.exe
pause
exit /b 0
:error
echo.
echo Не удалось собрать EXE. Проверьте Python и интернет.
pause
exit /b 1
