@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\tools\Configure-Game-Data.ps1" %*
set "WINDOWSMASH_SETUP_RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %WINDOWSMASH_SETUP_RESULT%
