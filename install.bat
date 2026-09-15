@echo off
REM Double-click to install Meridian as a standalone app: sets up its own
REM .venv (same as start.bat, skipped if that already ran), then adds a
REM desktop shortcut and starts it automatically at logon. Nothing is
REM installed outside this folder except that shortcut and the logon entry --
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
  echo   it also starts automatically the next time you sign in.
) else (
  echo   Something went wrong installing the shortcut -- see above.
)
echo.
pause
endlocal
