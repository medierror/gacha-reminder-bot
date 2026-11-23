@echo off
rem Builds dist\Gacha Tracker.exe (single file). Run from anywhere: double-click this file.
cd /d "%~dp0\.."
set ROOT=%CD%
if not exist ".venv-app\Scripts\python.exe" (
  echo Creating the build environment...
  py -3.11 -m venv .venv-app || python -m venv .venv-app
)
".venv-app\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r app\requirements-app.txt || goto :fail
".venv-app\Scripts\python.exe" app\make_icon.py || goto :fail
".venv-app\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed ^
  --name "Gacha Tracker" --icon "%ROOT%\app\icon.ico" ^
  --add-data "%ROOT%\app\ui;ui" --add-data "%ROOT%\app\icon.png;." ^
  --paths "%ROOT%" ^
  --hidden-import pystray._win32 --hidden-import keyring.backends.Windows --hidden-import win32ctypes.core ^
  --collect-data tzdata --collect-submodules genshin ^
  --distpath "%ROOT%\dist" --workpath "%ROOT%\build\pyi" --specpath "%ROOT%\build" ^
  "%ROOT%\app\tracker.py" || goto :fail
echo.
echo Done: %ROOT%\dist\Gacha Tracker.exe
exit /b 0
:fail
echo Build failed.
exit /b 1
