@echo off
REM ============================================================================
REM  ContextFS developer bootstrap + launcher   (log.md Phase 29, Decision 89)
REM
REM  FOR DEVELOPERS. End users get the installer (v2 Phase 4) and never see this.
REM
REM  Every run checks each prerequisite and installs ONLY what is missing:
REM    1. uv (Astral's standalone Python/package manager)   -> .tools\uv
REM    2. Python 3.12 (portable, no registry, no C: writes) -> .tools\python
REM       fallback: winget into .tools\python-winget; else stop with a link
REM    3. .venv  (created only if missing or broken)
REM    4. torch (CUDA build if a usable NVIDIA GPU is present, else CPU),
REM       contextfs + extras, spaCy model, embedding weights, smoke corpus
REM       -> scripts\dev_bootstrap.py does these checks
REM  then launches the app (or passes arguments straight to the CLI).
REM
REM  Everything lives inside this folder. Nothing is written to C:.
REM  The project path may contain spaces and '&': keep EVERY path quoted or
REM  relative to the project folder.
REM
REM  KEEP IN SYNC with any change to the start-up procedure.
REM ============================================================================

setlocal
cd /d "%~dp0"
chcp 65001 >nul 2>&1

set "UV_VERSION=0.13.6"
set "UV=.tools\uv\uv.exe"
set "PY=.venv\Scripts\python.exe"

REM All caches inside the project, never under %LOCALAPPDATA% or %USERPROFILE%.
set "UV_PYTHON_INSTALL_DIR=%CD%\.tools\python"
set "UV_CACHE_DIR=%CD%\.tools\cache\uv"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_LINK_MODE=copy"
set "PIP_CACHE_DIR=%CD%\.tools\cache\pip"
set "HF_HOME=%CD%\models\hf"
set "HF_HUB_DISABLE_TELEMETRY=1"
set "HF_HUB_DISABLE_XET=1"
set "PYTHONUTF8=1"

echo.
echo  ================================================================
echo    C O N T E X T F S   -   find files by what you remember
echo  ================================================================
echo.

REM ---------------------------------------------------------------------------
REM  1. uv
REM ---------------------------------------------------------------------------
if exist "%UV%" goto :have_uv
echo  [ SETUP ] Downloading uv %UV_VERSION% into .tools\uv ...
where curl.exe >nul 2>&1 || (echo  [ ERROR ] curl.exe not found ^(needs Windows 10 1803 or later^). & goto :fail)
where tar.exe  >nul 2>&1 || (echo  [ ERROR ] tar.exe not found ^(needs Windows 10 1803 or later^). & goto :fail)
if not exist ".tools\uv" mkdir ".tools\uv"
curl.exe -sSfL --retry 2 -o ".tools\uv\uv.zip" "https://github.com/astral-sh/uv/releases/download/%UV_VERSION%/uv-x86_64-pc-windows-msvc.zip"
if errorlevel 1 (echo  [ ERROR ] Could not download uv from github.com. Check the internet connection. & goto :fail)
tar.exe -xf ".tools\uv\uv.zip" -C ".tools\uv"
if errorlevel 1 (echo  [ ERROR ] Could not unpack uv. Is the disk full? & goto :fail)
del ".tools\uv\uv.zip" >nul 2>&1
:have_uv

REM ---------------------------------------------------------------------------
REM  2 + 3. Python and the virtual environment
REM ---------------------------------------------------------------------------
if exist "%PY%" (
    "%PY%" -c "import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)" >nul 2>&1
    if not errorlevel 1 goto :have_venv
    echo  [ SETUP ] The existing .venv is broken or the wrong Python version. Rebuilding it.
)

echo  [ SETUP ] Installing portable Python 3.12 into .tools\python ...
"%UV%" python install 3.12 --no-bin --no-registry
if errorlevel 1 goto :python_fallback
"%UV%" venv ".venv" --python 3.12 --seed --clear
if errorlevel 1 (echo  [ ERROR ] Could not create .venv. Is the disk full? & goto :fail)
goto :have_venv

:python_fallback
echo  [ WARN  ] uv could not install Python. Trying winget ...
where winget >nul 2>&1 || goto :no_python
winget install -e --id Python.Python.3.12 --scope user --location "%CD%\.tools\python-winget" --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :no_python
".tools\python-winget\python.exe" -m venv --clear ".venv"
if errorlevel 1 (echo  [ ERROR ] Could not create .venv. Is the disk full? & goto :fail)
goto :have_venv

:no_python
echo  [ ERROR ] Could not install Python 3.12 automatically.
echo            Install Python 3.12 from https://www.python.org/downloads/
echo            then delete the .venv folder and run start.bat again.
goto :fail

:have_venv

REM ---------------------------------------------------------------------------
REM  4. Everything else (torch build, package, models, smoke corpus)
REM ---------------------------------------------------------------------------
"%PY%" scripts\dev_bootstrap.py
if errorlevel 1 goto :fail

REM ---------------------------------------------------------------------------
REM  Smoke index - ContextFS needs something to show on a fresh checkout.
REM ---------------------------------------------------------------------------
if not exist ".contextfs\contextfs.db" (
    echo  [ INDEX ] No index yet. Building one over the smoke corpus...
    "%PY%" -m contextfs scan
    if errorlevel 1 (echo  [ ERROR ] Index build failed - see the output above. & goto :fail)
)

REM ---------------------------------------------------------------------------
REM  Pass-through: `start.bat query "..."` runs the CLI and exits.
REM ---------------------------------------------------------------------------
if "%~1"=="" goto :menu
if /i "%~1"=="gui"  goto :gui
if /i "%~1"=="app"  goto :gui
if /i "%~1"=="3d"   goto :viz
if /i "%~1"=="test" goto :test
if /i "%~1"=="eval" goto :eval
if /i "%~1"=="check" goto :check
"%PY%" -m contextfs %*
exit /b %errorlevel%

:check
"%PY%" scripts\dev_bootstrap.py --check
exit /b %errorlevel%

:menu
echo.
echo  ----------------------------------------------------------------
echo    [1]  Desktop application
echo    [2]  Command line
echo    [3]  3D relationship graph
echo    [4]  Re-scan / update the index
echo    [5]  Run the evaluation (smoke fixture)
echo    [6]  Run the test suite
echo    [Q]  Quit
echo  ----------------------------------------------------------------
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
goto :menu

:gui
"%PY%" -m contextfs gui
goto :done

:shell
echo  ContextFS command line. Try:   contextfs query "the PDF I studied before my ML exam" --explain
echo  Type `exit` to close this shell.
cmd /k "".venv\Scripts\activate.bat" && doskey contextfs=python -m contextfs $*"
goto :done

:viz
"%PY%" -m contextfs visualise
goto :done

:scan
"%PY%" -m contextfs scan
goto :done

:eval
"%PY%" scripts\evaluate.py
goto :done

:test
"%PY%" -m pytest -q
goto :done

:fail
echo.
echo  Setup stopped. Fix the problem above and run start.bat again -
echo  steps that already succeeded will be skipped.
echo.
pause
exit /b 1

:done
echo.
pause
exit /b 0
