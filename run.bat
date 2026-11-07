@echo off
REM False-Arb Checker on LIVE data: LLM frame (Gemini) + numeric core (arbcheck) + polysim.
REM
REM   run.bat               set up, run all tests, start the app on live Polymarket data  (http://127.0.0.1:8080)
REM   run.bat kalshi        same, plus Kalshi (ONLY if you are a Kalshi member, on your own computer)
REM   run.bat monitor       scan live markets every 5 minutes; log every suggestion; re-price confirmed pairs
REM   run.bat monitor-kalshi   the monitor, plus Kalshi (members only)
REM   run.bat test          set up and run every test (core, polysim, frame), then stop
REM   run.bat core          the core's own app, no LLM frame (http://127.0.0.1:8765)
REM
REM Needs GEMINI_API_KEY (Google AI Studio) in .env. Paper only: nothing here places orders.
setlocal EnableExtensions
cd /d "%~dp0"
set "MODE=%~1"
if "%MODE%"=="" set "MODE=app"
set "PY=.venv\Scripts\python.exe"

REM ---------- 1. Python environment ----------
if not exist "%PY%" (
    echo [setup] creating .venv
    where uv >nul 2>nul
    if not errorlevel 1 (
        uv venv .venv --python 3.11 || goto :fail
    ) else (
        python -m venv .venv || goto :fail
    )
)
"%PY%" -c "import nicegui, google.genai, pydantic, numpy, dotenv, pytest, requests" >nul 2>nul
if errorlevel 1 (
    echo [setup] installing requirements-frame.txt
    where uv >nul 2>nul
    if not errorlevel 1 (
        uv pip install --python "%PY%" -r requirements-frame.txt || goto :fail
    ) else (
        "%PY%" -m pip install -r requirements-frame.txt || goto :fail
    )
)

REM ---------- 2. API key ----------
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo [setup] created .env from .env.example
)
set "HASKEY=0"
findstr /r /c:"^GEMINI_API_KEY=..*" ".env" >nul 2>nul && set "HASKEY=1"

if /i "%MODE%"=="core" goto :core

REM ---------- 3. Tests ----------
if /i "%MODE%"=="monitor" goto :keycheck
if /i "%MODE%"=="monitor-kalshi" goto :keycheck
echo.
echo [test] core: arbcheck
pushd arbcheck-project
"..\%PY%" -m pytest -q -p no:warnings || (popd & goto :fail)
popd
echo [test] polysim + frame (the live Gemini smoke test runs only if a key is set)
"%PY%" -m pytest tests -q -p no:warnings || goto :fail
if /i "%MODE%"=="test" goto :done

:keycheck
if "%HASKEY%"=="0" (
    echo.
    echo [setup] No GEMINI_API_KEY in .env. Put your Google AI Studio key in .env as GEMINI_API_KEY=... and run again.
    goto :fail
)

REM ---------- 4. Run ----------
set "KFLAG="
if /i "%MODE%"=="kalshi" set "KFLAG=--kalshi-member"
if /i "%MODE%"=="monitor-kalshi" set "KFLAG=--kalshi-member"
if /i "%MODE:~0,7%"=="monitor" (
    echo.
    echo [monitor] live markets every 5 minutes %KFLAG%   Ctrl+C to stop. Log: data\frame\suggestions.jsonl
    "%PY%" -m frame.monitor %KFLAG%
    goto :done
)
echo.
echo [app] http://127.0.0.1:8080   live data %KFLAG%   Ctrl+C to stop
start "" "http://127.0.0.1:8080"
"%PY%" -m app_frame.main %KFLAG%
goto :done

:core
echo [core] arbcheck's own app on live data: http://127.0.0.1:8765   Ctrl+C to stop
start "" "http://127.0.0.1:8765"
pushd arbcheck-project
"..\%PY%" -m arbcheck serve
popd
goto :done

:fail
echo.
echo FAILED. See the messages above.
endlocal
exit /b 1

:done
endlocal
exit /b 0
