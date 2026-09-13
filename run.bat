@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title 5imp1e 5atebox - launcher

set "PY="

echo [1/3] Checking Python...
py -3 -c "import sys; print(sys.version.split()[0])" >nul 2>nul
if not errorlevel 1 (
    set "PY=py -3"
    goto :python_ok
)

python -c "import sys; print(sys.version.split()[0])" >nul 2>nul
if not errorlevel 1 (
    set "PY=python"
    goto :python_ok
)

python3 -c "import sys; print(sys.version.split()[0])" >nul 2>nul
if not errorlevel 1 (
    set "PY=python3"
    goto :python_ok
)

echo [ERROR] A working Python 3 interpreter was not found.
echo The Windows Store alias may be shadowing your real Python installation.
echo Try running: py -3 --version
pause
exit /b 1

:python_ok
for /f "delims=" %%V in ('%PY% -c "import sys; print(sys.version.split()[0])"') do set "PYVER=%%V"
echo Python %PYVER% found via: %PY%

echo.
echo [2/3] Checking PySide6...
%PY% -c "import PySide6; print(PySide6.__version__)" > "%TEMP%\515_pyside_check.txt" 2>&1
if errorlevel 1 goto :install_pyside
for /f "usebackq delims=" %%V in ("%TEMP%\515_pyside_check.txt") do set "PYSIDEVER=%%V"
del "%TEMP%\515_pyside_check.txt" >nul 2>nul
echo PySide6 %PYSIDEVER% found.
goto :start_app

:install_pyside
del "%TEMP%\515_pyside_check.txt" >nul 2>nul
echo PySide6 not found. Installing requirements...
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto :install_failed
%PY% -c "import PySide6; print('PySide6 ' + PySide6.__version__ + ' installed successfully.')"
if errorlevel 1 goto :install_failed

:start_app
echo.
echo [3/3] Starting 5imp1e 5atebox...
echo.
%PY% main.py
set "APP_EXIT=%ERRORLEVEL%"
if not "%APP_EXIT%"=="0" (
    echo.
    echo [ERROR] The application exited with code %APP_EXIT%.
    echo Copy the traceback above and send it to me.
    pause
    exit /b %APP_EXIT%
)
exit /b 0

:install_failed
echo.
echo [ERROR] Dependency installation failed.
echo Check pip and your internet connection, then try again.
pause
exit /b 1
