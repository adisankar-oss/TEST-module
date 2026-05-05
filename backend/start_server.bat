@echo off
setlocal

for %%I in ("%~dp0..") do set "PROJECT_ROOT=%%~fI"
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  echo Virtualenv interpreter not found:
  echo   %VENV_PYTHON%
  echo Create the virtualenv first, then rerun this script.
  exit /b 1
)

echo Starting backend with:
echo   %VENV_PYTHON%

"%VENV_PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
