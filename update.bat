@echo off
rem ============================================================
rem  MIDAS - one-click project update from an AI-exported zip
rem
rem  Usage:
rem    update.bat              uses the newest .zip in this folder
rem    update.bat my-code.zip  or drag ^& drop a zip onto this file
rem
rem  1. Extracts the zip into a temp staging folder first - if
rem     extraction fails, the project is left untouched.
rem  2. Backs up the current source to _update_backup\<stamp>,
rem     then mirrors the new source over the old one so stale
rem     source files are removed cleanly.
rem  3. Deletes old build/dev output (app\build, app\.dart_tool,
rem     engine\build, engine\dist, dist).
rem  4. NEVER touches tools\, vendor\, data\ or engine\.venv -
rem     portable tools and caches survive every update.
rem  5. Restarts whatever you last ran - build.bat or
rem     run_dev.bat - remembered in data\last_action.txt.
rem ============================================================
setlocal enabledelayedexpansion

rem -- Run from a temp copy so the zip may safely replace update.bat itself.
if /i "%~1"=="--staged" goto :staged
copy /y "%~f0" "%TEMP%\midas-update-run.bat" >nul || (
    echo [X] Could not stage the updater in TEMP.
    pause
    exit /b 1
)
"%TEMP%\midas-update-run.bat" --staged "%~dp0." "%~1"
exit /b %errorlevel%

:staged
set "ROOT=%~2"
set "ZIPARG=%~3"
cd /d "%ROOT%" || (echo [X] Project folder not found: %ROOT% & pause & exit /b 1)

echo.
echo  ============================================
echo    MIDAS update - fresh gold, same vault
echo  ============================================
echo.

rem ---------- [1/6] locate the update zip ----------
echo [1/6] Locating the update zip...
set "ZIP="
if not "%ZIPARG%"=="" (
    if exist "%ZIPARG%" (
        set "ZIP=%ZIPARG%"
    ) else (
        echo [X] The zip you passed does not exist: %ZIPARG%
        pause
        exit /b 1
    )
)
if not defined ZIP for /f "delims=" %%F in ('dir /b /a-d /o-d "*.zip" 2^>nul') do if not defined ZIP set "ZIP=%CD%\%%F"
if not defined ZIP (
    echo [X] No .zip found in the project root.
    echo     Put the new code zip next to update.bat - or drag ^& drop it onto update.bat - and run again.
    pause
    exit /b 1
)
echo   - Using: !ZIP!

rem ---------- [2/6] extract to a temp staging folder ----------
echo [2/6] Extracting to a temp staging folder...
set "STAGE=%TEMP%\midas-update-stage"
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%" || goto :fail
tar -xf "!ZIP!" -C "%STAGE%" >nul 2>nul
if errorlevel 1 (
    echo   - tar could not read it; trying PowerShell Expand-Archive...
    powershell -NoProfile -Command "Expand-Archive -Force -LiteralPath '!ZIP!' -DestinationPath '%STAGE%'" || goto :fail
)

rem The zip may wrap everything in a single top folder - find the real root.
set "SRC="
if exist "%STAGE%\engine\main.py" set "SRC=%STAGE%"
if exist "%STAGE%\app\pubspec.yaml" set "SRC=%STAGE%"
if not defined SRC for /d %%D in ("%STAGE%\*") do (
    if exist "%%D\engine\main.py" set "SRC=%%D"
    if exist "%%D\app\pubspec.yaml" set "SRC=%%D"
)
if not defined SRC (
    echo [X] This zip does not look like the Midas codebase.
    echo     Expected engine\main.py or app\pubspec.yaml inside. Nothing was changed.
    goto :fail
)
echo   - New source root: !SRC!

rem ---------- [3/6] back up the current source ----------
echo [3/6] Backing up the current source...
set "STAMP="
for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "STAMP=%%T"
if not defined STAMP set "STAMP=%RANDOM%%RANDOM%"
set "BACKUP=%CD%\_update_backup\!STAMP!"
robocopy "%CD%" "!BACKUP!" /E /XD tools vendor data dist build .venv .dart_tool __pycache__ _update_backup node_modules /XF *.zip >nul
if errorlevel 8 (
    echo [X] Backup failed - stopping before touching anything.
    goto :fail
)
echo   - Backup: !BACKUP!

rem ---------- [4/6] remove old build output ----------
echo [4/6] Removing old build/dev output...
for %%D in ("app\build" "app\.dart_tool" "engine\build" "engine\dist" "dist") do (
    if exist "%%~D" (
        echo   - Deleting %%~D
        rmdir /s /q "%%~D"
    )
)

rem ---------- [5/6] install the new source ----------
echo [5/6] Installing the new source - tools\, vendor\, data\ and engine\.venv are preserved...
for /f "delims=" %%D in ('dir /b /ad "!SRC!" 2^>nul') do (
    set "SKIP="
    for %%P in (tools vendor data _update_backup node_modules) do if /i "%%D"=="%%P" set "SKIP=1"
    if not defined SKIP (
        echo   - Updating %%D\ ...
        call :mirror "!SRC!\%%D" "%CD%\%%D"
        if errorlevel 1 goto :rollback
    )
)
for /f "delims=" %%F in ('dir /b /a-d "!SRC!" 2^>nul') do (
    copy /y "!SRC!\%%F" "%CD%\%%F" >nul
    if errorlevel 1 goto :rollback
)

rem ---------- [6/6] finish + restart the last action ----------
echo [6/6] Finishing up...
move /y "!ZIP!" "!BACKUP!\" >nul 2>nul
rmdir /s /q "%STAGE%" 2>nul
echo.
echo  Update complete. The previous source was saved to:
echo    !BACKUP!
echo.

set "LAST="
if exist "data\last_action.txt" set /p LAST=<"data\last_action.txt"
if /i "!LAST!"=="build" (
    echo Last action was a release BUILD - starting build.bat...
    call build.bat
    exit /b !errorlevel!
)
if /i "!LAST!"=="dev" (
    echo Last action was the DEV server - starting run_dev.bat...
    call run_dev.bat
    exit /b !errorlevel!
)
echo No previous build/dev action recorded yet.
choice /c BDN /n /m "Start the [B]uild, the [D]ev server, or [N]othing? "
if errorlevel 3 exit /b 0
if errorlevel 2 (
    call run_dev.bat
    exit /b !errorlevel!
)
call build.bat
exit /b !errorlevel!

rem ---------- helpers ----------
:mirror
rem Mirror one source folder onto the project. Protected folders are
rem excluded from both copying and deletion so they always survive
rem (tools, vendor, data, .venv, caches, build output, the marker file).
robocopy %1 %2 /MIR /XD tools vendor data .venv .dart_tool build dist __pycache__ node_modules /XF last_action.txt >nul
if errorlevel 8 exit /b 1
exit /b 0

:rollback
echo.
echo [X] Copying the new source failed - restoring the previous source...
robocopy "!BACKUP!" "%CD%" /E >nul
echo     Restored from the backup. The project is back to its previous state.
goto :fail

:fail
echo.
echo  ********************************************
echo   Update FAILED - see the messages above.
echo  ********************************************
if exist "%STAGE%" rmdir /s /q "%STAGE%" 2>nul
pause
exit /b 1
