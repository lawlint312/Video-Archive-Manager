@echo off
REM Is it running, and where? The hidden launchers show nothing by design, so
REM "I double-clicked it and nothing happened" looks exactly like a failure.
REM This is how you tell the difference.
setlocal enabledelayedexpansion
title Video Archive Manager - status
cd /d "%~dp0"

if "%VAM_PORT%"=="" set VAM_PORT=8301

echo.
echo   Video Archive Manager - status
echo   =============================
echo.

set ALIVE=0
set ADDR=
for /f "tokens=2" %%a in ('netstat -ano -p tcp ^| findstr /c:":%VAM_PORT% " ^| findstr /c:"LISTENING"') do (
    set ALIVE=1
    set ADDR=%%a
)

if "%ALIVE%"=="0" (
    echo   [--]  NOT running  ^(nothing is listening on port %VAM_PORT%^)
    echo.
    echo         Start it with start.bat, or check the newest log below.
) else (
    echo   [ok]  RUNNING      !ADDR!
    echo.
    echo         http://localhost:%VAM_PORT%/
    echo !ADDR! | findstr /c:"0.0.0.0" >nul && (
        echo.
        echo         LAN mode - other devices can reach it at:
        for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
            REM ipconfig's value comes back with a leading space, which lands in
            REM the middle of the URL and stops it being copyable.
            set IP=%%a
            set IP=!IP: =!
            echo !IP! | findstr /r "192\.168\. 10\." >nul && echo           http://!IP!:%VAM_PORT%/
        )
    )
)

echo.
echo   --- newest log -------------------------------------------------
for /f "delims=" %%f in ('dir /b /o-d "data\logs\*_LOG.txt" 2^>nul') do (
    echo   data\logs\%%f
    echo.
    powershell -NoProfile -Command "Get-Content 'data\logs\%%f' -Tail 12" 2>nul
    goto :afterlog
)
echo   (no log files yet)
:afterlog

echo.
echo   --- console output from the newest hidden start ------------------
for /f "delims=" %%f in ('dir /b /o-d "data\logs\*_CONSOLE.txt" 2^>nul') do (
    echo   data\logs\%%f
    echo.
    powershell -NoProfile -Command "Get-Content 'data\logs\%%f' -Tail 12" 2>nul
    goto :afterconsole
)
echo   (none - only the hidden launchers write this)
:afterconsole

echo.
pause
exit /b 0
