@echo off
REM False-Arb Checker: LLM frame (Gemini) + numeric core (arbcheck) + polysim. Paper only: nothing places orders.
REM
REM Double-click for a menu, or:  run.bat <mode>
REM   app | kalshi             the app on LIVE data (opens the Board). Stops any old copy first, picks a free port,
REM                            and re-runs the tests only if the code changed since they last passed.
REM   monitor | monitor-kalshi the smart watch loop, no browser
REM   testdata-convert | testdata-core | testdata-app     downloaded test data (Gemini only maps file layouts)
REM   test                     every test, always       core   the core's own app, no LLM
REM   stop | status | help
REM
REM Kalshi on/off lives in .env: FRAME_KALSHI_MEMBER=1 (ONLY if you are a Kalshi member, on your own computer).
REM Needs GEMINI_API_KEY (Google AI Studio) in .env for every mode that uses Gemini.
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
set "MODE=%~1"
set "MENU=0"
set "PY=.venv\Scripts\python.exe"
set "L=%PY% scripts\launcher.py"
if /i "%MODE%"=="help" goto :help
if /i "%MODE%"=="-h" goto :help
if /i "%MODE%"=="/?" goto :help
if not "%MODE%"=="" goto :setup

:menu
set "MENU=1"
echo.
echo  False-Arb Checker  (paper only, no orders are placed)
echo  ------------------------------------------------------------
if exist "%PY%" %L% status --short
echo   1  App on live markets: opens the Board          (stops any old copy)
echo   2  App with Kalshi on for this run               (members only)
echo   3  Monitor: smart watch loop, no browser
echo   4  Monitor with Kalshi on for this run           (members only)
echo   5  Test data: convert downloaded files           (Gemini maps the layout)
echo   6  Test data: the core on converted data         (no Gemini)
echo   7  Test data: the app on converted data
echo   8  Run every test
echo   9  The core's own app, no LLM frame
echo   S  Stop all servers
echo   0  Exit
echo.
choice /c 123456789S0 /n /m "Pick [1-9, S to stop, 0 to exit]: "
set "PICK=%errorlevel%"
if "%PICK%"=="1" set "MODE=app"
if "%PICK%"=="2" set "MODE=kalshi"
if "%PICK%"=="3" set "MODE=monitor"
if "%PICK%"=="4" set "MODE=monitor-kalshi"
if "%PICK%"=="5" set "MODE=testdata-convert"
if "%PICK%"=="6" set "MODE=testdata-core"
if "%PICK%"=="7" set "MODE=testdata-app"
if "%PICK%"=="8" set "MODE=test"
if "%PICK%"=="9" set "MODE=core"
if "%PICK%"=="10" set "MODE=stop"
if "%PICK%"=="11" goto :done

:setup
REM ---------- 1. Python environment (only does work when something is missing) ----------
if not exist "%PY%" (
    echo [setup] creating .venv
    where uv >nul 2>nul
    if not errorlevel 1 ( uv venv .venv --python 3.11 || goto :fail ) else ( python -m venv .venv || goto :fail )
)
"%PY%" -c "import nicegui, google.genai, pydantic, numpy, dotenv, pytest, requests" >nul 2>nul
if errorlevel 1 (
    echo [setup] installing requirements-frame.txt ^(first run only^)
    where uv >nul 2>nul
    if not errorlevel 1 ( uv pip install --python "%PY%" -r requirements-frame.txt || goto :fail ) else ( "%PY%" -m pip install -r requirements-frame.txt || goto :fail )
)
if /i "%MODE%"=="stop" goto :stop
if /i "%MODE%"=="status" goto :status

REM ---------- 2. .env, key, Kalshi ----------
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo [setup] created .env from .env.example
)
set "KFLAG="
set "KSTATE=Kalshi off ^(set FRAME_KALSHI_MEMBER=1 in .env to turn it on^)"
findstr /r /c:"^FRAME_KALSHI_MEMBER=1" ".env" >nul 2>nul && set "KSTATE=Kalshi ON ^(from .env^)"
if /i "%MODE%"=="kalshi" set "KFLAG=--kalshi-member" & set "KSTATE=Kalshi ON ^(this run^)"
if /i "%MODE%"=="monitor-kalshi" set "KFLAG=--kalshi-member" & set "KSTATE=Kalshi ON ^(this run^)"

if /i "%MODE%"=="test" goto :tests
if /i "%MODE%"=="core" goto :core
if /i "%MODE%"=="testdata-core" goto :tdcore
if /i "%MODE%"=="testdata-convert" goto :needkey
if /i "%MODE%"=="testdata-app" goto :needkey
if /i "%MODE:~0,7%"=="monitor" goto :needkey
if /i "%MODE%"=="app" goto :needkey
if /i "%MODE%"=="kalshi" goto :needkey
echo Unknown mode "%MODE%".
goto :help

:needkey
%L% has-key
if errorlevel 1 (
    echo.
    echo [setup] No GEMINI_API_KEY in .env. Put your Google AI Studio key in .env as GEMINI_API_KEY=... and run again.
    goto :fail
)
if /i "%MODE:~0,7%"=="monitor" goto :monitor
if /i "%MODE%"=="testdata-convert" goto :tdconvert
if /i "%MODE%"=="testdata-app" goto :tdapp

REM ---------- 3. The app (live data) ----------
:app
%L% needs-tests
if not errorlevel 1 (
    echo [test] the code changed since the tests last passed: running them now ^(they are skipped when nothing changed^)
    call :runtests || goto :fail
) else (
    echo [test] code unchanged since the tests last passed: skipped
)
echo [servers] freeing the port from any old copy of the app
%L% stop --apps
for /f %%p in ('%L% port 8080') do set "PORT=%%p"
echo [kalshi] %KSTATE%
echo [app] LIVE data. http://127.0.0.1:!PORT!/board  ^(it starts scanning by itself; Ctrl+C or run.bat stop to stop^)
start "" /b %L% open !PORT! /board
"%PY%" -m app_frame.main --port !PORT! %KFLAG%
echo.
echo [app] stopped ^(exit code !errorlevel!^)
goto :done

:monitor
echo [servers] freeing the port from any old copy
%L% stop --apps
echo [kalshi] %KSTATE%
echo [monitor] smart watch loop on live markets. Ctrl+C to stop. Log: data\frame\suggestions.jsonl
"%PY%" -m frame.monitor %KFLAG%
goto :done

:tdconvert
echo [testdata] converting every file in data\testdata\raw  ^(Gemini proposes a field mapping; code copies the numbers^)
"%PY%" -m frame.testdata convert
goto :done

:tdapp
%L% stop --apps
for /f %%p in ('%L% port 8080') do set "PORT=%%p"
echo [app] http://127.0.0.1:!PORT!  TEST DATA from data\testdata\normalized  ^(not live^)   Ctrl+C to stop
start "" /b %L% open !PORT! /board
"%PY%" -m app_frame.main --testdata --port !PORT!
goto :done

:tdcore
echo [testdata] the core on converted data in data\testdata\normalized  ^(no Gemini^)
"%PY%" -m frame.testdata core
goto :done

:core
echo [core] arbcheck's own app on live data, %KSTATE%
for /f %%p in ('%L% port 8765') do set "PORT=%%p"
echo [core] http://127.0.0.1:!PORT!   Ctrl+C to stop
start "" /b %L% open !PORT! /
pushd arbcheck-project
"..\%PY%" -m arbcheck serve %KFLAG% --port !PORT!
popd
goto :done

:tests
call :runtests || goto :fail
goto :done

:runtests
echo [test] core: arbcheck
pushd arbcheck-project
"..\%PY%" -m pytest -q -p no:warnings
if errorlevel 1 ( popd & exit /b 1 )
popd
echo [test] polysim + frame ^(the live Gemini smoke test runs only if a key is set^)
"%PY%" -m pytest tests -q -p no:warnings
if errorlevel 1 exit /b 1
%L% mark-tests-ok
exit /b 0

:stop
%L% stop
goto :done

:status
%L% status
goto :done

:help
echo.
echo  run.bat            menu
echo  run.bat app        live app, opens the Board     run.bat kalshi   same with Kalshi on (members only)
echo  run.bat monitor    smart watch loop              run.bat monitor-kalshi
echo  run.bat testdata-convert / testdata-core / testdata-app
echo  run.bat test       every test                    run.bat core     the core's own app
echo  run.bat stop       stop all our servers          run.bat status   what is running
goto :done

:fail
echo.
echo FAILED. See the messages above.
if "%MENU%"=="1" pause
endlocal
exit /b 1

:done
if "%MENU%"=="1" pause
endlocal
exit /b 0
