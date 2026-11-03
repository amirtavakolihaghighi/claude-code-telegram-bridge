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
echo     1   Status          (is it running? is it keeping up?)
echo     2   Start           (in the background; installs it if needed)
echo     3   Stop
echo     4   Restart
echo.
echo     5   Install         (start by itself at every sign-in)
echo     6   Uninstall       (stop doing that)
echo.
echo     7   Show log        (last 40 lines)
echo     8   Watch log       (live, Ctrl+C to stop)
echo     9   Run here        (in this window instead, Ctrl+C to stop)
echo.
echo    10   Send missed history  (everything never sent, oldest first)
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
if "%choice%"=="10" call :run catch-up
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
rem  Asks the bridge itself rather than Task Scheduler, which says "Running"
rem  for a copy that is stuck, and knows nothing of one started with 9.
for /f "delims=" %%S in ('"%PY%" -m tools.autostart brief') do echo     %%S
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
rem  Only one copy may run at a time; a second one would refuse to start.
"%PY%" -m tools.autostart running >nul 2>&1
if not errorlevel 1 (
    echo.
    "%PY%" -m tools.autostart running
    choice /C YN /M "  Stop it and run here instead"
    if errorlevel 2 goto :eof
    "%PY%" -m tools.autostart stop
    if errorlevel 1 goto :eof
)
echo.
echo   Running in this window. Close it or press Ctrl+C to stop.
echo   Afterwards choose 2 to have it carry on in the background.
echo.
"%PY%" -m bridge
goto :eof
