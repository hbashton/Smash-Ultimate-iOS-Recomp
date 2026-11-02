@echo off
rem Builds the game from your own files in one go: the setup guide's steps from installing the
rem Python packages to the build itself (docs\BUILDING.md), one after the other. It asks where your
rem game files, keys and firmware are, and stops with a message at the first step that fails. A
rem step that is already done is skipped, so running it again carries on where it stopped. Needs
rem Python 3.10 or newer and Visual Studio's Clang tools (the guide's first steps), or what
rem "Install WindowSmash.bat" set up. tools\build_game.py does the work; options such as --jobs 4
rem are passed on to it.
setlocal
cd /d "%~dp0.."
call tools\find_python.bat || (
    echo.
    pause
    exit /b 1
)
%PY% tools\build_game.py %*
set "result=%ERRORLEVEL%"
echo.
pause
exit /b %result%
