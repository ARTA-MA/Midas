@echo off
rem ============================================================
rem  MIDAS - development mode
rem  Downloads any missing prerequisites automatically, starts
rem  the Python engine (port 8765, no watchdog) in its own
rem  window, then runs the Flutter app in debug mode.
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem Remember the last action so update.bat can restart it after an update.
if not exist data mkdir data
>data\last_action.txt echo dev

echo.
echo  MIDAS dev mode
echo  --------------

echo [1/3] Getting prerequisites (auto-download if missing)...
call scripts\bootstrap.bat || (pause & exit /b 1)
rem Keep Dart/Flutter packages in a Midas-only cache OUTSIDE the project.
rem A cache inside the project tree gets recorded with relative paths, which
rem the Windows release build cannot resolve (the "Error when reading ..."
rem failures), and the machine-wide AppData cache had broken entries too.
set "PUB_CACHE=%LOCALAPPDATA%\Midas\pub-cache"
if not exist "%PUB_CACHE%" mkdir "%PUB_CACHE%"
rem Heal half-extracted packages: a package folder that exists but has no
rem lib\ makes "pub get" report success while the compile then fails with
rem "Error when reading ...: The system cannot find the path specified".
for /d %%D in ("%PUB_CACHE%\hosted\pub.dev\*") do if not exist "%%D\lib" (
    echo   - Repairing incomplete package: %%~nxD
    rmdir /s /q "%%D"
)
rem The in-project cache from the previous version is no longer used.
if exist "tools\pub-cache" rmdir /s /q "tools\pub-cache"

if not exist engine\.venv (
    echo   - Creating Python environment...
    "%PYTHON_CMD%" -m venv engine\.venv || (pause & exit /b 1)
)
engine\.venv\Scripts\python.exe -m pip install --quiet -r engine\requirements.txt || (pause & exit /b 1)

call scripts\get_fonts.bat || (pause & exit /b 1)

echo [2/3] Starting the Midas engine on http://127.0.0.1:8765 ...
start "Midas Engine (dev)" cmd /k "engine\.venv\Scripts\python.exe engine\main.py --port 8765 --no-watchdog"

echo [3/3] Launching Flutter (debug)...
pushd app
if not exist windows\CMakeLists.txt (
    echo   - Creating the Windows runner project ^(one time^)...
    call "%FLUTTER_CMD%" create --platforms=windows --project-name midas . || (popd & pause & exit /b 1)
    copy /y windows\midas.ico windows\runner\resources\app_icon.ico >nul
)
rem Clear stale package-resolution state (old paths break the compile after
rem the project folder moves or the machine-wide cache changes).
if exist .dart_tool rmdir /s /q .dart_tool
if exist build rmdir /s /q build
if exist .flutter-plugins del /q .flutter-plugins
if exist .flutter-plugins-dependencies del /q .flutter-plugins-dependencies
call "%FLUTTER_CMD%" pub get || (popd & pause & exit /b 1)
call "%FLUTTER_CMD%" run -d windows
popd
