@echo off
REM The server itself: one process, serving both the API and the built frontend.
REM
REM Runs uvicorn in THIS console rather than starting a new window, which is
REM what lets start-hidden.vbs hide it completely -- a `start` here would open a
REM window the vbs cannot hide. Double-click this file directly and you get the
REM log on screen, which is the point when something is wrong.
REM
REM VAM_HOST decides which interface it listens on. 127.0.0.1 (the default)
REM means this PC only.
setlocal
cd /d "%~dp0"

if "%VAM_HOST%"=="" set VAM_HOST=127.0.0.1
REM VAM_PORT exists so a second instance can be run alongside the real one
REM (testing a change without stopping the service you are using).
if "%VAM_PORT%"=="" set VAM_PORT=8301

REM Rebuild only when the sources are newer -- ~3.6 s when they are, nothing
REM when they are not. Without this the backend would quietly serve whatever
REM dist/ happened to contain, which is the one real hazard of dropping the
REM dev server.
python "%~dp0tools\needs_build.py" >nul 2>&1
if errorlevel 1 (
    echo   frontend out of date, rebuilding...
    call "%~dp0build.bat"
    if errorlevel 1 exit /b 1
)

REM Refuse to start a second copy. Without this, double-clicking the hidden
REM launcher twice starts a process that writes one line to a fresh log file,
REM fails to bind the port, and dies -- leaving two logs and no explanation.
netstat -ano -p tcp | findstr /c:":%VAM_PORT% " | findstr /c:"LISTENING" >nul
if not errorlevel 1 (
    echo   already running on port %VAM_PORT% - nothing to do
    exit /b 0
)

cd /d "%~dp0backend"
python -m uvicorn app.main:app --host %VAM_HOST% --port %VAM_PORT%
