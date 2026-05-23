@echo off
rem One-click release. Usage:
rem   release.bat              -> show usage
rem   release.bat 1.0.1        -> release version 1.0.1
rem   release.bat 1.0.1 "Fix bug X"
rem   release.bat patch        -> auto-bump patch (1.0.0 -> 1.0.1)
rem   release.bat minor        -> auto-bump minor (1.0.5 -> 1.1.0)
rem   release.bat major        -> auto-bump major (1.5.2 -> 2.0.0)

setlocal

if "%~1"=="" (
    echo Usage:
    echo   release.bat ^<version^> [notes]      e.g. release.bat 1.0.1 "Fix login bug"
    echo   release.bat patch                  e.g. auto-bump patch
    echo   release.bat minor
    echo   release.bat major
    exit /b 1
)

if /I "%~1"=="patch" (
    python release.py --bump patch --notes "%~2"
    exit /b %errorlevel%
)
if /I "%~1"=="minor" (
    python release.py --bump minor --notes "%~2"
    exit /b %errorlevel%
)
if /I "%~1"=="major" (
    python release.py --bump major --notes "%~2"
    exit /b %errorlevel%
)

python release.py "%~1" --notes "%~2"
