@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First start: installing the bot, please wait...
  py -3 -m venv .venv || python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if not exist ".env" copy ".env.example" ".env" >nul
findstr /c:"paste-your-bot-token-here" ".env" >nul && (
  echo.
  echo Paste your bot token into the .env file that just opened, save it, then run start.bat again.
  notepad ".env"
  pause
  exit /b
)
".venv\Scripts\python.exe" bot.py
pause
