@echo off
REM Double-click to install Meridian as a standalone app: sets up its own
REM .venv (same as start.bat, skipped if that already ran), then adds a
REM desktop shortcut, starts it automatically at logon, and registers a task
REM that keeps it updated from GitHub with no further clicks (see launch.py
REM and install.py for exactly what that checks and how often). Nothing is
REM installed outside this folder except that shortcut and the task --
REM see install.py --uninstall to remove them.
setlocal
cd /d "%~dp0"

set PY=
where py >nul 2>nul && set PY=py
if not defined PY (where python >nul 2>nul && set PY=python)
if not defined PY (
  echo.
  echo   Python was not found on this machine.
  echo.
  echo   Install it from https://www.python.org/downloads/ and tick
  echo   "Add python.exe to PATH" in the installer, then run this again.
  echo.
  pause
  exit /b 1
)

echo.
echo   Setting up Meridian...
echo.
%PY% start.py --setup-only
if errorlevel 1 (
  pause
  exit /b 1
)

echo.
echo   Adding a desktop shortcut and starting Meridian...
echo.
.venv\Scripts\python.exe install.py
set RC=%errorlevel%

echo.
if %RC%==0 (
  echo   Installed. Look for the Meridian icon on your desktop --
  echo   it starts automatically the next time you sign in, and keeps
  echo   itself updated from GitHub with no further clicks.
) else (
  echo   Meridian is installed but did not start cleanly -- see above.
)
echo.
pause
endlocal
