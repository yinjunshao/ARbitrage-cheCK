@echo off
REM False-Arb Checker: LLM frame (Gemini) + numeric core (arbcheck) + polysim. Paper only: nothing places orders.
REM
REM Double-click run.bat for a numbered menu, or pass a mode:
REM   run.bat app | kalshi | monitor | monitor-kalshi | testdata-convert | testdata-core | testdata-app | test | core
REM
REM Kalshi on/off lives in .env: FRAME_KALSHI_MEMBER=1 (ONLY if you are a Kalshi member, on your own computer).
REM Needs GEMINI_API_KEY (Google AI Studio) in .env for every mode that uses Gemini.
setlocal EnableExtensions
cd /d "%~dp0"
set "MODE=%~1"
set "MENU=0"
set "PY=.venv\Scripts\python.exe"
if not "%MODE%"=="" goto :setup

:menu
set "MENU=1"
echo.
echo  False-Arb Checker  (paper only, no orders are placed)
echo  ------------------------------------------------------------
echo   1  App on live markets                    http://127.0.0.1:8080
echo   2  App on live markets, Kalshi on for this run (members only)
echo   3  Monitor: smart watch loop on live markets, no browser
echo   4  Monitor with Kalshi on for this run (members only)
echo   5  Test data: convert downloaded files (Gemini maps the format)
echo   6  Test data: run the core on converted data (no Gemini)
echo   7  Test data: app on converted data
echo   8  Run every test
echo   9  The core's own app, no LLM frame        http://127.0.0.1:8765
echo   0  Exit
echo.
choice /c 1234567890 /n /m "Pick a mode [1-9, 0 to exit]: "
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
if "%PICK%"=="10" goto :done

:setup
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

REM ---------- 2. .env and key ----------
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo [setup] created .env from .env.example
)
set "HASKEY=0"
findstr /r /c:"^GEMINI_API_KEY=..*" ".env" >nul 2>nul && set "HASKEY=1"
set "KFLAG="
set "KSTATE=Kalshi off (FRAME_KALSHI_MEMBER=1 in .env to turn it on)"
findstr /r /c:"^FRAME_KALSHI_MEMBER=1" ".env" >nul 2>nul && set "KSTATE=Kalshi ON (from .env)"
if /i "%MODE%"=="kalshi" set "KFLAG=--kalshi-member"
if /i "%MODE%"=="monitor-kalshi" set "KFLAG=--kalshi-member"
if defined KFLAG set "KSTATE=Kalshi ON (this run)"

if /i "%MODE%"=="core" goto :core
if /i "%MODE%"=="testdata-core" goto :tdcore
if /i "%MODE%"=="test" goto :tests
if /i "%MODE%"=="app" goto :tests
if /i "%MODE%"=="kalshi" goto :tests
goto :keycheck

REM ---------- 3. Tests (before the app, and for "test") ----------
:tests
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
echo [kalshi] %KSTATE%
if /i "%MODE%"=="testdata-convert" (
    echo.
    echo [testdata] converting every file in data\testdata\raw  ^(Gemini proposes a field mapping; code copies the numbers^)
    "%PY%" -m frame.testdata convert
    goto :done
)
if /i "%MODE%"=="testdata-app" (
    echo.
    echo [app] http://127.0.0.1:8080   TEST DATA from data\testdata\normalized   Ctrl+C to stop
    start "" "http://127.0.0.1:8080"
    "%PY%" -m app_frame.main --testdata
    goto :done
)
if /i "%MODE:~0,7%"=="monitor" (
    echo.
    echo [monitor] smart watch loop on live markets %KFLAG%   Ctrl+C to stop. Log: data\frame\suggestions.jsonl
    "%PY%" -m frame.monitor %KFLAG%
    goto :done
)
echo.
echo [app] http://127.0.0.1:8080   live data %KFLAG%   Ctrl+C to stop
start "" "http://127.0.0.1:8080"
"%PY%" -m app_frame.main %KFLAG%
goto :done

:tdcore
echo.
echo [testdata] the core on converted data in data\testdata\normalized  (no Gemini)
"%PY%" -m frame.testdata core
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
if "%MENU%"=="1" pause
endlocal
exit /b 1

:done
if "%MENU%"=="1" pause
endlocal
exit /b 0
