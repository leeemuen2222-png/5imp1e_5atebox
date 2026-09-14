@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title 5imp1e 5atebox Launcher

echo ==================================================
echo   5imp1e 5atebox / 515 launcher
echo ==================================================
echo.

echo [1/5] Detecting Python...
set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3"
if not defined PY_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
    where python3 >nul 2>nul
    if not errorlevel 1 set "PY_CMD=python3"
)
if not defined PY_CMD (
    echo.
    echo [ERROR] Python 3 was not found.
    echo Install Python 3, enable "Add Python to PATH", then run this file again.
    echo.
    pause
    exit /b 1
)
%PY_CMD% --version
if errorlevel 1 goto :python_error

echo.
echo [2/5] Checking core UI dependency...
%PY_CMD% -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo PySide6 not found. Installing...
    %PY_CMD% -m pip install --disable-pip-version-check PySide6
    if errorlevel 1 goto :install_error
) else (
    echo PySide6 OK.
)

echo.
echo [3/5] Checking lightweight 3D renderer...
%PY_CMD% -c "import numpy, pyqtgraph, pyqtgraph.opengl, OpenGL" >nul 2>nul
if errorlevel 1 (
    echo 3D packages missing. Installing pyqtgraph, PyOpenGL and numpy...
    %PY_CMD% -m pip install --disable-pip-version-check numpy pyqtgraph PyOpenGL
    if errorlevel 1 goto :install_error
    echo Verifying 3D packages...
    %PY_CMD% -c "import numpy, pyqtgraph, pyqtgraph.opengl, OpenGL" >nul 2>nul
    if errorlevel 1 goto :install_error
) else (
    echo pyqtgraph / PyOpenGL / numpy OK.
)

echo.
echo [4/5] Checking rigid-body physics engine...
%PY_CMD% -c "import culverin" >nul 2>nul
if errorlevel 1 (
    echo Culverin / Jolt Physics not found. Installing prebuilt wheel...
    %PY_CMD% -m pip install --disable-pip-version-check "culverin==0.14.0"
    if errorlevel 1 goto :install_error
    echo Verifying Culverin...
    %PY_CMD% -c "import culverin; print('Culverin', getattr(culverin, '__version__', 'OK'))"
    if errorlevel 1 goto :install_error
) else (
    %PY_CMD% -c "import culverin; print('Culverin / Jolt Physics OK -', getattr(culverin, '__version__', 'installed'))"
)

echo.
echo [5/5] Starting 5imp1e 5atebox...
if not exist "main.py" (
    echo.
    echo [ERROR] main.py was not found in:
    echo %CD%
    echo Rename the downloaded main^&*.py file to main.py and place it here.
    echo.
    pause
    exit /b 1
)
%PY_CMD% "main.py"
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
    echo.
    echo [ERROR] The application exited with code %APP_EXIT%.
    echo The console is being kept open so you can read the error above.
    echo.
    pause
)
exit /b %APP_EXIT%

:python_error
echo.
echo [ERROR] Python was detected but could not be started correctly.
pause
exit /b 1

:install_error
echo.
echo [ERROR] A required package could not be installed.
echo Check your internet connection and pip configuration, then run this launcher again.
echo.
echo Note: this build uses Culverin / Jolt Physics because it provides a
echo prebuilt Windows wheel for Python 3.14 and does not require Visual C++.
echo.
pause
exit /b 1
