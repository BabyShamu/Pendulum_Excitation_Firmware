@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 exit /b 1

if not exist ".venv-build\Scripts\python.exe" (
    python -m venv .venv-build
    if errorlevel 1 goto failed
)
set "BUILD_PYTHON=%CD%\.venv-build\Scripts\python.exe"

"%BUILD_PYTHON%" -m pip install -e ".[build]"
if errorlevel 1 goto failed

if exist "build" rmdir /s /q "build"
if exist "build" goto failed
if exist "dist" rmdir /s /q "dist"
if exist "dist" goto failed

"%BUILD_PYTHON%" -m PyInstaller --clean --noconfirm --workpath build --distpath dist PendulumWorkbench.spec
if errorlevel 1 goto failed
if not exist "dist\Pendulum Workbench\Pendulum Workbench.exe" goto failed

echo.
echo Built: "%CD%\dist\Pendulum Workbench\Pendulum Workbench.exe"
echo Distribute the entire "dist\Pendulum Workbench" folder, not just the EXE.
popd
pause
exit /b 0

:failed
echo.
echo Build failed. Review the error above.
popd
pause
exit /b 1