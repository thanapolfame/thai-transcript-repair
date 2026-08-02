@echo off
rem Windows: double-click this file to open the GUI.  The first run builds
rem .venv and installs the dependencies; later runs go straight to the browser.

rem gui.py prints Thai; without these the console dies on a UnicodeEncodeError.
rem This file's own messages stay ASCII, since a batch file's text is read back
rem under whatever code page was active when it started.
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

setlocal
cd /d "%~dp0"

set VENV=.venv
set PY=%VENV%\Scripts\python.exe

if not exist "%PY%" (
    echo First run: setting up, this takes a few minutes...

    rem "py" is the launcher that ships with the python.org installer and the
    rem only reliable way to ask for a specific version on Windows.
    py -3.13 -m venv "%VENV%" 2>nul || py -3 -m venv "%VENV%" 2>nul || python -m venv "%VENV%" 2>nul
    if not exist "%PY%" goto nopython

    "%PY%" -m pip install --quiet --upgrade pip
    "%PY%" -m pip install --quiet -r requirements.txt
    if errorlevel 1 goto failed
    echo Setup complete.
)

"%PY%" gui.py %*
goto end

:nopython
echo.
echo Python 3.13 or newer is required.  Install it from
echo     https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" while installing.
goto end

:failed
echo.
echo Setup failed - see the messages above.

:end
echo.
pause
