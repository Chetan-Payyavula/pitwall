@echo off
rem One-click setup + start for Pitwall Telemetry (Windows).
rem First run: creates .venv, installs packages, runs tests. Later runs: just starts the site.
setlocal
cd /d "%~dp0"
set LOG=%~dp0setup-log.txt
echo ==== %date% %time% ==== > "%LOG%"

rem --- find Python 3.10+ (py launcher first, then python) ---
set PY=
py -3 -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set PY=py -3
if not defined PY python -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul && set PY=python
if not defined PY (
  echo PYTHON_MISSING >> "%LOG%"
  echo.
  echo  Python 3.10+ was not found.
  echo  Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo  then double-click run.bat again.
  echo.
  pause
  exit /b 1
)
%PY% --version >> "%LOG%" 2>&1

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv >> "%LOG%" 2>&1 || (echo VENV_FAILED >> "%LOG%" & echo Could not create .venv - see setup-log.txt & pause & exit /b 1)
)
set VPY=.venv\Scripts\python.exe

echo Installing packages (first time takes a minute or two)...
%VPY% -m pip install --upgrade pip >> "%LOG%" 2>&1
%VPY% -m pip install -r requirements.txt >> "%LOG%" 2>&1 || (echo PIP_FAILED >> "%LOG%" & echo Package install failed - see setup-log.txt & pause & exit /b 1)
echo PIP_OK >> "%LOG%"

echo Running tests...
%VPY% -m pytest -q -p no:warnings >> "%LOG%" 2>&1 && (echo TESTS_OK >> "%LOG%") || (echo TESTS_FAILED >> "%LOG%")

echo.
echo  Starting Pitwall at http://localhost:8000   (close this window to stop it)
echo.
echo SERVER_STARTING >> "%LOG%"
start "" /b cmd /c "timeout /t 5 >nul & start http://localhost:8000"
%VPY% -m uvicorn backend.main:app --port 8000 --reload --reload-dir backend
pause
