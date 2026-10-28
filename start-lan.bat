@echo off
REM Start VAM so other devices on the same network (a phone, a tablet) can
REM reach it, instead of this PC only.
REM
REM Deliberately a separate launcher. Listening on every interface is a
REM standing change to who can see the login page, so it should be something
REM you choose each time -- double-clicking the normal start.bat keeps the
REM service local, as before.
REM
REM Note the traffic is plain HTTP: anyone on the same network can read it.
REM Fine at home; do not do this on a network you do not control.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set VAM_HOST=0.0.0.0

echo.
echo   Video Archive Manager - LAN mode
echo   ===============================
echo   Reachable from other devices on this network.
echo.

REM show the addresses a phone can actually use (skips WSL/Hyper-V adapters)
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    REM the value comes back with a leading space; left in, it lands in the
    REM middle of the URL and stops it being copyable
    set IP=%%a
    set IP=!IP: =!
    echo !IP! | findstr /r "192\.168\. 10\." >nul && echo     http://!IP!:8301/
)
echo.

call start.bat
