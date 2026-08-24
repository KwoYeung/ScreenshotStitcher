@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating project Python environment...
    py -3 -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

".venv\Scripts\python.exe" app.py
exit /b %errorlevel%

:error
echo.
echo Setup failed. Check that Python 3.10 or newer is installed.
pause
exit /b 1
