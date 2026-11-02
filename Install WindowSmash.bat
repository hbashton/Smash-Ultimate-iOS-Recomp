@echo off
rem Sets WindowSmash up on this PC in one go: brings this folder up to date from GitHub, checks the
rem PC, installs what is missing (Python, the Visual Studio build tools, SDL3), asks where your game
rem files, keys and firmware are (found by itself when yuzu or Ryujinx has them), builds the game,
rem and puts a shortcut on the desktop. Nothing but Windows itself is needed to start it. A step that
rem is already done is skipped, so running it again carries on where it stopped, and run later it
rem updates WindowSmash and builds again. tools\install_windowsmash.ps1 does the work; options such
rem as -DryRun are passed on to it (see its header).
setlocal
cd /d "%~dp0"
rem Files downloaded from the internet are marked, and Windows then warns about each one it
rem runs: take the mark off every file in this folder first.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -LiteralPath '%~dp0.' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue"
rem The installer may update this folder, this file included, and cmd reads a batch file as it goes:
rem from here on everything is one block, which cmd reads whole before running it.
(
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_windowsmash.ps1" %*
    if errorlevel 1 (
        echo.
        pause
        exit /b 1
    )
    echo.
    pause
    exit /b 0
)
