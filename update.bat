@echo off
REM Double-click to update Meridian from GitHub, then start it.
REM Plain Python -- no PowerShell.
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (py start.py --update %* & goto :done)
where python >nul 2>nul && (python start.py --update %* & goto :done)
echo.
echo   Python was not found. Install it from https://www.python.org/downloads/
echo   and tick "Add python.exe to PATH", then run this again.
echo.
pause
exit /b 1
:done
if errorlevel 1 pause
endlocal
