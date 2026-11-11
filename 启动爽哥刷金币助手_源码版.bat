@echo off
title Shuage Coin Helper V2 - Source Launcher
cd /d "%~dp0"

rem Python lookup: 1) Python 3.12 per-user install  2) pythonw in PATH  3) custom path below
rem Python 3.12 is required - other versions may miss the pinned dependencies.
set "PYW="

if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"

if not defined PYW for %%I in (pythonw.exe) do if not "%%~$PATH:I"=="" set "PYW=%%~$PATH:I"

rem set "PYW=D:\Python312\pythonw.exe"

if not defined PYW (
    echo [ERROR] pythonw.exe not found.
    echo Install Python 3.12 and add it to PATH, or set PYW in this file.
    pause
    exit /b 1
)

rem Locate the main entry next to this launcher (filename is Chinese, so use a wildcard
rem instead of a literal to stay independent of the batch file code page).
set "APP="
for %%F in ("%~dp0*.py") do set "APP=%%~fF"

if not defined APP (
    echo [ERROR] main .py entry not found next to this launcher.
    pause
    exit /b 1
)

start "" "%PYW%" "%APP%"
