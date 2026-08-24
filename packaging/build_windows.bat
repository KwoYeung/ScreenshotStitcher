@echo off
setlocal
cd /d "%~dp0\.."

set "BUILD_VENV=.venv-build-windows"
set "BUILD_DIR=build\windows"
set "DIST_DIR=release\windows"

if not exist "%BUILD_VENV%\Scripts\python.exe" (
    echo Creating isolated Windows build environment...
    py -3 -m venv "%BUILD_VENV%" || goto :error
)

"%BUILD_VENV%\Scripts\python.exe" -m pip install --upgrade pip || goto :error
"%BUILD_VENV%\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-build.txt || goto :error

if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
if exist "%DIST_DIR%\ScreenshotStitcher.exe" del /q "%DIST_DIR%\ScreenshotStitcher.exe"

"%BUILD_VENV%\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --workpath "%BUILD_DIR%" ^
    --distpath "%DIST_DIR%" ^
    packaging\windows.spec || goto :error

echo.
echo Windows executable created:
echo %CD%\%DIST_DIR%\ScreenshotStitcher.exe
pause
exit /b 0

:error
echo.
echo Packaging failed. Review the messages above.
pause
exit /b 1
