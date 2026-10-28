@echo off
REM HTTPS front for VAM (Caddy + Let's Encrypt via Cloudflare DNS).
REM See caddy\Caddyfile.example for why it is set up this way.
REM
REM Runs Caddy in THIS console, like run-server.bat does for uvicorn, so the
REM hidden launcher can capture everything it prints into data\logs.
setlocal
cd /d "%~dp0"

set VAM_CADDY_STORAGE=%~dp0data\caddy
set VAM_CADDY_ACCESS_LOG=%~dp0data\logs\caddy_access.log
if not exist "%~dp0data\caddy" mkdir "%~dp0data\caddy"
if not exist "%~dp0data\logs" mkdir "%~dp0data\logs"

REM The plain winget build has no DNS provider modules, so this uses the
REM build downloaded from caddyserver.com with github.com/caddy-dns/cloudflare.
set CADDY=%~dp0tools\caddy\caddy.exe
if not exist "%CADDY%" (
    echo   %CADDY% not found.
    echo   Download a build that includes the Cloudflare DNS module:
    echo   https://caddyserver.com/download  ^(add package github.com/caddy-dns/cloudflare^)
    exit /b 1
)

REM The token is never written in a tracked file. An empty or missing file
REM would otherwise surface as a vague ACME error much later.
set TOKEN_FILE=%~dp0data\caddy\cloudflare_token.txt
if not exist "%TOKEN_FILE%" (
    echo   missing %TOKEN_FILE% - paste the Cloudflare API token into it
    exit /b 1
)
set /p CF_API_TOKEN=<"%TOKEN_FILE%"
if "%CF_API_TOKEN%"=="" (
    echo   %TOKEN_FILE% is empty - paste the Cloudflare API token into it
    exit /b 1
)

REM The live config names this machine's domain, so it is not tracked; the
REM tracked template uses vam.example.com.
if not exist "%~dp0caddy\Caddyfile" (
    echo   missing caddy\Caddyfile - copy caddy\Caddyfile.example and put in your domain
    exit /b 1
)

REM Same guard as run-server.bat: a second copy would fail to bind 443 and
REM leave a log that explains nothing.
netstat -ano -p tcp | findstr /c:":443 " | findstr /c:"LISTENING" >nul
if not errorlevel 1 (
    echo   something is already listening on 443 - nothing to do
    exit /b 0
)

"%CADDY%" run --config "%~dp0caddy\Caddyfile" --adapter caddyfile
