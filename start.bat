@echo off
setlocal
title Video Archive Manager - launcher
cd /d "%~dp0"

REM Everyday launcher: starts the server minimised and opens the browser.
REM For no window at all, put start-hidden.vbs in the Startup folder instead
REM (see README-startup.txt).
REM
REM One server now, not two: the backend serves the built frontend, so there is
REM no Vite process in normal use. Development still uses Vite -- start-dev.bat.

if "%VAM_HOST%"=="" set VAM_HOST=127.0.0.1
if "%VAM_PORT%"=="" set VAM_PORT=8301

echo.
echo   Video Archive Manager
echo   =====================
echo.

call :listening %VAM_PORT%
if "%ALIVE%"=="1" (
    echo   [ok]  already running   http://127.0.0.1:%VAM_PORT%
    goto :open
)

echo   [..]  starting...       http://127.0.0.1:%VAM_PORT%
start "VAM" /min /d "%~dp0" cmd /k run-server.bat

echo.
echo   waiting for the server to come up...
echo   (a frontend rebuild adds a few seconds the first time after an update)
set /a tries=0
:wait
call :listening %VAM_PORT%
if "%ALIVE%"=="1" goto :open
set /a tries+=1
if %tries% geq 60 goto :timeout
REM ping, not timeout: timeout aborts when stdin is redirected
ping -n 2 127.0.0.1 >nul
goto :wait

:open
echo.
echo   ready - opening http://localhost:%VAM_PORT%/
start "" http://localhost:%VAM_PORT%/
echo.
echo   One minimised window is now running the server.
echo   Close it (or run stop.bat) to shut the service down.
ping -n 5 127.0.0.1 >nul
exit /b 0

:timeout
echo.
echo   [!!] the server did not answer within 60 seconds.
echo        Check the "VAM" window for the error
echo        (first run may need:  cd frontend  ^&^&  npm install)
echo.
pause
exit /b 1

REM ---- helper: is anything listening on port %1? -----------------------
:listening
set ALIVE=0
netstat -ano -p tcp | findstr /c:":%1 " | findstr /c:"LISTENING" >nul && set ALIVE=1
exit /b 0
