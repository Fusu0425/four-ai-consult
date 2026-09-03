@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
  echo The local Python environment is missing.
  echo Run: py -m venv .venv
  echo Then: .venv\Scripts\python.exe -m pip install -r requirements.txt
  pause
  exit /b 1
)

"%PYTHON%" "%~dp0main.py"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Four AI Consult exited with code %EXIT_CODE%.
  echo Please copy the error above when asking for help.
  pause
)

endlocal & exit /b %EXIT_CODE%
