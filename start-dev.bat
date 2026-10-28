@echo off
setlocal
title Video Archive Manager - dev
cd /d "%~dp0"

REM Development launcher: Vite on 5173 with hot reload, proxying /api to the
REM backend on 8301. Two windows, as before.
REM
REM This is the only thing that still needs the Vite dev server. Everyday use
REM goes through start.bat, where the backend serves the built frontend and
REM there is no Node process at all.

if "%VAM_HOST%"=="" set VAM_HOST=127.0.0.1

echo.
echo   Video Archive Manager - DEV
echo   ===========================
echo   Vite hot reload on 5173, backend on 8301.
echo.

call :listening 8301
if "%ALIVE%"=="1" (
    echo   [ok]  backend   already running
) else (
    echo   [..]  backend   starting...
    start "VAM backend" /min /d "%~dp0backend" cmd /k python -m uvicorn app.main:app --host %VAM_HOST% --port 8301
)

call :listening 5173
if "%ALIVE%"=="1" (
    echo   [ok]  vite      already running
    goto :open
)
echo   [..]  vite      starting...
start "VAM vite" /min /d "%~dp0frontend" cmd /k npm run dev

echo.
echo   waiting for Vite...
set /a tries=0
:wait
call :listening 5173
if "%ALIVE%"=="1" goto :open
set /a tries+=1
if %tries% geq 60 goto :timeout
ping -n 2 127.0.0.1 >nul
goto :wait

:open
echo.
start "" http://localhost:5173/
ping -n 3 127.0.0.1 >nul
exit /b 0

:timeout
echo.
echo   [!!] Vite did not answer within 60 seconds.
pause
exit /b 1

:listening
set ALIVE=0
netstat -ano -p tcp | findstr /c:":%1 " | findstr /c:"LISTENING" >nul && set ALIVE=1
exit /b 0
