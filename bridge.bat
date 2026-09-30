@echo off
setlocal EnableDelayedExpansion
pushd "%~dp0"

set "PY=.venv\Scripts\python.exe"
set "LOG=runtime\bridge.log"

if not exist "%PY%" (
    echo.
    echo   Cannot find %PY%
    echo   Create the environment first:
    echo       python -m venv .venv
    echo       .venv\Scripts\python -m pip install -r requirements.txt
    echo.
    pause
    popd
    exit /b 1
)

rem  Allow "bridge.bat status" as well as the menu.
if not "%~1"=="" (
    call :run %~1
    popd
    exit /b !errorlevel!
)

:menu
cls
echo.
echo   ============================================
echo      Claude Code  -^>  Telegram  bridge
echo   ============================================
echo.
call :short_status
echo.
echo     1   Status          (is it running?)
echo     2   Start
echo     3   Stop
echo     4   Restart
echo.
echo     5   Install         (start automatically at sign-in)
echo     6   Uninstall       (stop doing that)
echo.
echo     7   Show log        (last 40 lines)
echo     8   Watch log       (live, Ctrl+C to stop)
echo     9   Run here        (in this window, Ctrl+C to stop)
echo.
echo     0   Exit
echo.
set "choice="
set /p "choice=  Choose: "

if "%choice%"=="1" call :run status
if "%choice%"=="2" call :run start
if "%choice%"=="3" call :run stop
if "%choice%"=="4" call :restart
if "%choice%"=="5" call :run install
if "%choice%"=="6" call :run uninstall
if "%choice%"=="7" call :showlog
if "%choice%"=="8" call :watchlog
if "%choice%"=="9" call :foreground
if "%choice%"=="0" goto :done

if not "%choice%"=="0" (
    echo.
    pause
    goto :menu
)

:done
popd
endlocal
exit /b 0

rem ---------------------------------------------------------------- helpers

:short_status
schtasks /Query /TN "ClaudeCodeTelegramBridge" >nul 2>&1
if errorlevel 1 (
    echo     Not installed  -  choose 5 to have it start by itself
    goto :eof
)
for /f "tokens=2 delims=:" %%S in ('schtasks /Query /TN "ClaudeCodeTelegramBridge" /FO LIST ^| findstr /B /C:"Status:"') do (
    set "state=%%S"
)
set "state=!state: =!"
if /i "!state!"=="Running" (
    echo     Running
) else (
    echo     Installed but not running  -  choose 2 to start
)
goto :eof

:run
"%PY%" -m tools.autostart %~1
goto :eof

:restart
"%PY%" -m tools.autostart stop
timeout /t 3 /nobreak >nul
"%PY%" -m tools.autostart start
goto :eof

:showlog
if not exist "%LOG%" (
    echo   No log yet - the bridge has not run with a log file.
    goto :eof
)
echo.
powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 40"
goto :eof

:watchlog
if not exist "%LOG%" (
    echo   No log yet - the bridge has not run with a log file.
    goto :eof
)
echo.
echo   Watching %LOG%  -  press Ctrl+C to stop.
echo.
powershell -NoProfile -Command "Get-Content '%LOG%' -Tail 20 -Wait"
goto :eof

:foreground
echo.
echo   Running in this window. Close it or press Ctrl+C to stop.
echo   (If the background copy is running too, stop it first with 3.)
echo.
"%PY%" -m bridge
goto :eof
