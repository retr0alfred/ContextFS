@echo off
REM ============================================================================
REM  ContextFS launcher
REM
REM  One entry point for the whole project. Sets up the environment on first
REM  run, then offers the three surfaces: desktop app, CLI, 3D graph.
REM
REM  KEEP THIS FILE IN SYNC. If the start-up procedure changes anywhere -
REM  a new dependency, a new model, a changed command - change it here too.
REM  A launcher that silently drifts from reality is worse than none, because
REM  it fails on the one machine that has never been set up before.
REM ============================================================================

setlocal enabledelayedexpansion
cd /d "%~dp0"

REM UTF-8 code page so the box-drawing glyphs render instead of falling back to
REM ASCII. ContextFS degrades gracefully without this, but it looks better with.
chcp 65001 >nul 2>&1

set "PY=.venv\Scripts\python.exe"
set "MARKER=.venv\.contextfs-ready"

echo.
echo  ================================================================
echo    C O N T E X T F S
echo    find files by what you remember
echo  ================================================================
echo.

REM ---------------------------------------------------------------------------
REM  First-run setup
REM ---------------------------------------------------------------------------
if not exist "%PY%" (
    echo  [ SETUP ] No virtual environment found. Creating one...
    echo.
    where python >nul 2>&1
    if errorlevel 1 (
        echo  ERROR: Python is not on your PATH.
        echo  Install Python 3.10-3.12 from python.org, tick "Add to PATH",
        echo  then run this file again.
        echo.
        pause
        exit /b 1
    )
    python -m venv .venv
    if errorlevel 1 (
        echo  ERROR: could not create the virtual environment.
        pause
        exit /b 1
    )
    echo  [ SETUP ] Virtual environment created.
    echo.
)

if not exist "%MARKER%" (
    echo  [ SETUP ] Installing dependencies. This takes a few minutes once.
    echo.

    echo  [ 1/4 ] PyTorch ^(CPU-only build - ContextFS never uses a GPU^)...
    "%PY%" -m pip install --quiet --disable-pip-version-check "torch>=2.2,<3" --index-url https://download.pytorch.org/whl/cpu
    if errorlevel 1 goto :setupfailed

    echo  [ 2/4 ] ContextFS and its dependencies...
    "%PY%" -m pip install --quiet --disable-pip-version-check -e ".[dev,datagen,gui]"
    if errorlevel 1 goto :setupfailed

    echo  [ 3/4 ] Language model ^(en_core_web_md, ~40 MB^)...
    "%PY%" -m spacy download en_core_web_md --quiet
    if errorlevel 1 goto :setupfailed

    echo  [ 4/4 ] Embedding model...
    "%PY%" -m contextfs fetch-models
    if errorlevel 1 goto :setupfailed

    echo ok> "%MARKER%"
    echo.
    echo  [ SETUP ] Complete.
    echo.
)

REM ---------------------------------------------------------------------------
REM  Demo corpus - ContextFS needs something to index.
REM  Only ever writes into data\synthetic\corpus. Your own files are untouched.
REM ---------------------------------------------------------------------------
if not exist "data\synthetic\corpus" (
    echo  [ DATA ] Generating the 40-file demo corpus...
    "%PY%" scripts\generate_corpus.py >nul
    echo  [ DATA ] Done.
    echo.
)

REM ---------------------------------------------------------------------------
REM  Index
REM ---------------------------------------------------------------------------
if not exist ".contextfs\contextfs.db" (
    echo  [ INDEX ] No index yet. Building one - about 30 seconds.
    echo.
    "%PY%" -m contextfs scan
    echo.
)

REM ---------------------------------------------------------------------------
REM  Direct pass-through: `start.bat query "..."` runs the CLI and exits.
REM  Anything this script does not recognise is handed straight to the CLI, so
REM  every subcommand works without being listed here.
REM ---------------------------------------------------------------------------
if not "%~1"=="" (
    if /i "%~1"=="gui"  goto :gui
    if /i "%~1"=="app"  goto :gui
    if /i "%~1"=="3d"   goto :viz
    if /i "%~1"=="test" goto :test
    if /i "%~1"=="eval" goto :eval
    "%PY%" -m contextfs %*
    exit /b !errorlevel!
)

REM ---------------------------------------------------------------------------
REM  Menu
REM ---------------------------------------------------------------------------
:menu
echo  ----------------------------------------------------------------
echo    [1]  Desktop application
echo    [2]  Command line
echo    [3]  3D relationship graph
echo    [4]  Re-scan / update the index
echo    [5]  Run the research evaluation
echo    [6]  Run the test suite
echo    [Q]  Quit
echo  ----------------------------------------------------------------
echo.
set "choice="
set /p "choice=  Select: "
echo.

if /i "%choice%"=="1" goto :gui
if /i "%choice%"=="2" goto :shell
if /i "%choice%"=="3" goto :viz
if /i "%choice%"=="4" goto :scan
if /i "%choice%"=="5" goto :eval
if /i "%choice%"=="6" goto :test
if /i "%choice%"=="q" exit /b 0
echo  Not an option.
echo.
goto :menu

:gui
echo  Starting the desktop application...
echo  ^(models load once, about 25 seconds - then every search is instant^)
echo.
"%PY%" -m contextfs gui
goto :done

:shell
echo  ContextFS command line. Try:
echo.
echo     contextfs query "the PDF I studied before my ML exam" --explain
echo     contextfs query "notes from the ML exam" --compare
echo     contextfs timeline "March to April"
echo     contextfs digest
echo     contextfs --help
echo.
echo  Type `exit` to close this shell.
echo.
cmd /k ""%~dp0.venv\Scripts\activate.bat" && doskey contextfs="%~dp0.venv\Scripts\python.exe" -m contextfs $*"
goto :done

:viz
echo  Building the 3D relationship graph...
"%PY%" -m contextfs visualise
goto :done

:scan
"%PY%" -m contextfs scan
goto :done

:eval
echo  Running the full evaluation. Takes a couple of minutes.
echo.
"%PY%" scripts\evaluate.py
goto :done

:test
"%PY%" -m pytest -q
goto :done

:setupfailed
echo.
echo  ERROR: setup failed at the step above.
echo  Scroll up for the reason. Common causes: no internet connection,
echo  or Python is not 3.10-3.12.
echo.
pause
exit /b 1

:done
echo.
pause
exit /b 0
