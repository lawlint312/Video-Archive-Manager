@echo off
setlocal
title Video Archive Manager - stop

echo.
echo   Stopping Video Archive Manager...
echo.

call :kill 8301 server
call :kill 443 "caddy (https)"
REM 5173 only exists while start-dev.bat is running; harmless otherwise.
call :kill 5173 "vite (dev)"

REM the launcher's console windows, if they are still sitting there
taskkill /f /fi "WINDOWTITLE eq VAM*" >nul 2>&1

echo.
echo   done.
ping -n 4 127.0.0.1 >nul
exit /b 0

REM ---- helper: kill whatever listens on port %1 ------------------------
:kill
set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano -p tcp ^| findstr /c:":%1 " ^| findstr /c:"LISTENING"') do (
    taskkill /f /t /pid %%p >nul 2>&1
    echo   [ok]  %2  stopped  ^(port %1, pid %%p^)
    set FOUND=1
)
if "%FOUND%"=="0" echo   [--]  %2  was not running  ^(port %1^)
exit /b 0
