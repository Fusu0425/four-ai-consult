@echo off
setlocal
cd /d "%~dp0"

set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
set "LAUNCHER=%~dp0launcher.pyw"

if not exist "%PYTHONW%" (
  echo The local Python environment is missing.
  echo Run: py -m venv .venv
  echo Then: .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

if not exist "%LAUNCHER%" (
  echo The application launcher is missing: %LAUNCHER%
  pause
  exit /b 1
)

start "Four AI Consult" /D "%~dp0" "%PYTHONW%" "%LAUNCHER%"
endlocal
