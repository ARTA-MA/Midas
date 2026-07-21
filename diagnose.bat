@echo off
setlocal
title MIDAS Doctor
set "ROOT=%~dp0"
set "PY=%ROOT%engine\.venv\Scripts\python.exe"
if exist "%PY%" goto :run
set "PY=python"
where python >nul 2>nul
if errorlevel 1 goto :nopy
:run
echo.
echo Running MIDAS Doctor... this can take a few minutes. Please wait.
echo.
"%PY%" "%ROOT%scripts\diagnose.py"
echo.
pause
exit /b 0
:nopy
echo Could not find Python. Run run_dev.bat once first, then run this again.
pause
exit /b 1
