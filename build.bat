@echo off
REM Compile frontend/src into frontend/dist, which is what the backend serves.
REM
REM You do not normally need to run this by hand: run-server.bat checks whether
REM the sources are newer than dist/ and calls it for you. It is here for when
REM you want to rebuild without starting anything.
setlocal
cd /d "%~dp0frontend"

echo.
echo   building the frontend...
echo.
call npm run build
if errorlevel 1 (
    echo.
    echo   [!!] build failed. First run may need:  cd frontend  ^&^&  npm install
    echo.
    pause
    exit /b 1
)
echo.
echo   done.
exit /b 0
