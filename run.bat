@echo off
REM False-Arb Checker: LLM frame (Gemini) + numeric core (arbcheck), one command.
REM
REM   run.bat            set up, run all tests, start the app (demo markets; live Gemini if a key is set, else replay)
REM   run.bat replay     offline: demo markets + recorded Gemini outputs (no key, no network)
REM   run.bat demo       demo markets + live Gemini
REM   run.bat live       live Polymarket/Manifold through arbcheck's read-only client + live Gemini
REM   run.bat live-kalshi   as live, plus Kalshi (ONLY if you are a Kalshi member, on your own computer)
REM   run.bat test       set up and run every test, then stop
REM   run.bat core       the core's own app (arbcheck, no LLM frame) on http://127.0.0.1:8765
REM
REM Paper only. Nothing here places orders.
setlocal EnableExtensions
cd /d "%~dp0"
set "MODE=%~1"
if "%MODE%"=="" set "MODE=auto"
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
"%PY%" -c "import nicegui, google.genai, pydantic, numpy, dotenv, pytest" >nul 2>nul
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
    echo [setup] created .env from .env.example. Put your Google AI Studio key in .env as GEMINI_API_KEY=...
)
set "HASKEY=0"
findstr /r /c:"^GEMINI_API_KEY=..*" ".env" >nul 2>nul && set "HASKEY=1"

if /i "%MODE%"=="core" goto :core

REM ---------- 3. Tests: core (arbcheck) then frame ----------
echo.
echo [test] core: arbcheck
pushd arbcheck-project
"..\%PY%" -m pytest -q -p no:warnings || (popd & goto :fail)
popd
echo [test] frame (live Gemini smoke test runs only if a key is set)
"%PY%" -m pytest tests\frame -q -p no:warnings || goto :fail
if /i "%MODE%"=="test" goto :done

REM ---------- 4. App ----------
set "FLAG=--demo"
if /i "%MODE%"=="replay" set "FLAG=--replay"
if /i "%MODE%"=="live" set "FLAG=--live"
if /i "%MODE%"=="live-kalshi" set "FLAG=--live --kalshi-member"
if /i "%MODE%"=="auto" if "%HASKEY%"=="0" (
    echo [app] no GEMINI_API_KEY in .env, so starting in offline replay mode
    set "FLAG=--replay"
)
if not "%FLAG%"=="--replay" if "%HASKEY%"=="0" (
    echo [app] %MODE% needs GEMINI_API_KEY in .env. Use "run.bat replay" to run offline.
    goto :fail
)
echo.
echo [app] http://127.0.0.1:8080   (%FLAG%)   Ctrl+C to stop
start "" "http://127.0.0.1:8080"
"%PY%" -m app_frame.main %FLAG%
goto :done

:core
echo [core] arbcheck app on demo markets: http://127.0.0.1:8765   Ctrl+C to stop
start "" "http://127.0.0.1:8765"
pushd arbcheck-project
"..\%PY%" -m arbcheck serve --demo
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
