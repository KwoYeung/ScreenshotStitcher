@echo off
setlocal
cd /d "%~dp0\.."

set "APP_VERSION=1.1.1"
set "BUILD_VENV=.venv-build-windows"
set "BUILD_DIR=build\windows"
set "DIST_DIR=release\windows"
set "BUILD_OUTPUT=%DIST_DIR%\ScreenshotStitcher.exe"
set "RELEASE_EXE=%DIST_DIR%\ScreenshotStitcher-v%APP_VERSION%-Windows-x64.exe"

if not exist "%BUILD_VENV%\Scripts\python.exe" (
    where py >nul 2>nul || (
        echo Python 3 was not found. Install 64-bit Python 3.10 or newer first.
        goto :error
    )
    echo Creating isolated Windows build environment...
    py -3 -m venv "%BUILD_VENV%" || goto :error
)

"%BUILD_VENV%\Scripts\python.exe" -m pip install --upgrade pip || goto :error
"%BUILD_VENV%\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-build.txt || goto :error

if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
if exist "%BUILD_OUTPUT%" del /q "%BUILD_OUTPUT%"
if exist "%RELEASE_EXE%" del /q "%RELEASE_EXE%"

"%BUILD_VENV%\Scripts\python.exe" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --workpath "%BUILD_DIR%" ^
    --distpath "%DIST_DIR%" ^
    packaging\windows.spec || goto :error

if not exist "%BUILD_OUTPUT%" (
    echo Expected executable was not created: %BUILD_OUTPUT%
    goto :error
)
move /y "%BUILD_OUTPUT%" "%RELEASE_EXE%" >nul || goto :error

echo.
echo Windows executable created:
echo %CD%\%RELEASE_EXE%
pause
exit /b 0

:error
echo.
echo Packaging failed. Review the messages above.
pause
exit /b 1
