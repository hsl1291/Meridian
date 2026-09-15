@echo off
REM Double-click to start Groundwork. Nothing is installed outside this folder.
setlocal
cd /d "%~dp0"

REM py is the Windows launcher and is what a normal python.org install provides.
where py >nul 2>nul && (py start.py %* & goto :done)
where python >nul 2>nul && (python start.py %* & goto :done)

echo.
echo   Python was not found on this machine.
echo.
echo   Install it from https://www.python.org/downloads/ and tick
echo   "Add python.exe to PATH" in the installer, then run this again.
echo.
pause
exit /b 1

:done
if errorlevel 1 pause
endlocal
