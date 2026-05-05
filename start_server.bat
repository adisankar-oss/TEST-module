@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
if "%PROJECT_ROOT:~-1%"=="\" set "PROJECT_ROOT=%PROJECT_ROOT:~0,-1%"
set "VENV_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PYTHON%" (
  echo Virtualenv interpreter not found:
  echo   %VENV_PYTHON%
  exit /b 1
)

echo Starting backend from project root with:
echo   %VENV_PYTHON%

"%VENV_PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
