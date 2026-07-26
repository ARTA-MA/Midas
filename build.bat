@echo off
rem ============================================================
rem  MIDAS - one-click Windows release build
rem  Downloads EVERYTHING it needs automatically (Python, the
rem  Flutter SDK, Git, Deno, yt-dlp, ffmpeg, fonts) into the
rem  repo's own tools\ and vendor\ folders. Fully portable.
rem  Result: dist\Midas\Midas.exe + dist\Midas-win64.zip
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem Remember the last action so update.bat can restart it after an update.
if not exist data mkdir data
>data\last_action.txt echo build

echo.
echo  ============================================
echo    MIDAS build - everything turns to gold
echo  ============================================
echo.

rem ---------- [1/8] prerequisites (auto-download) ----------
echo [1/8] Getting prerequisites (auto-download if missing)...
call scripts\bootstrap.bat || goto :fail
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
echo   - Package cache: %LOCALAPPDATA%\Midas\pub-cache
echo   - Flutter SDK: %FLUTTER_CMD%

rem ---------- [2/8] python venv ----------
echo [2/8] Creating Python environment for the engine...
if not exist engine\.venv (
    "%PYTHON_CMD%" -m venv engine\.venv || goto :fail
)
engine\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet || goto :fail
engine\.venv\Scripts\python.exe -m pip install --quiet -r engine\requirements.txt pyinstaller || goto :fail
echo   - Engine dependencies installed.

rem ---------- [3/8] package engine ----------
echo [3/8] Packaging the Midas engine (PyInstaller)...
pushd engine
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --name midas-engine ^
    --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto ^
    --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto ^
    --hidden-import uvicorn.lifespan.on main.py >nul || (popd & goto :fail)
popd
echo   - Engine packaged: engine\dist\midas-engine.exe

rem ---------- [4/8] vendor tools ----------
echo [4/8] Downloading portable tools (Deno, yt-dlp, ffmpeg)...
if not exist vendor mkdir vendor
if not exist vendor\yt-dlp.exe (
    echo   - yt-dlp...
    curl.exe -fL -sS -o vendor\yt-dlp.exe.tmp "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" || (del vendor\yt-dlp.exe.tmp >nul 2>nul & goto :fail)
    move /y vendor\yt-dlp.exe.tmp vendor\yt-dlp.exe >nul || goto :fail
)
if not exist vendor\deno.exe (
    echo   - Deno...
    curl.exe -fL -sS -o vendor\deno.zip "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip" || goto :fail
    powershell -NoProfile -Command "Expand-Archive -Force vendor\deno.zip vendor" || goto :fail
    del vendor\deno.zip
)
if not exist vendor\ffmpeg.exe (
    echo   - ffmpeg ^(large download, please wait^)...
    curl.exe -fL -sS -o vendor\ffmpeg.zip "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip" || goto :fail
    powershell -NoProfile -Command "Expand-Archive -Force vendor\ffmpeg.zip vendor\_ff; Copy-Item vendor\_ff\*\bin\ffmpeg.exe,vendor\_ff\*\bin\ffprobe.exe vendor; Remove-Item -Recurse -Force vendor\_ff" || goto :fail
    del vendor\ffmpeg.zip
)
echo   - Tools ready in vendor\

rem ---------- [5/8] fonts ----------
echo [5/8] Fetching bundled fonts...
call scripts\get_fonts.bat || goto :fail

rem ---------- [6/8] flutter build ----------
echo [6/8] Building the Flutter app (release)...
pushd app
if not exist windows\CMakeLists.txt (
    echo   - Creating the Windows runner project ^(one time^)...
    call "%FLUTTER_CMD%" create --platforms=windows --project-name midas . || (popd & goto :fail)
    rem A failed CALL does not always set errorlevel, so verify the result:
    rem without CMakeLists.txt there is nothing for MSVC to compile.
    if not exist windows\CMakeLists.txt (
        echo   ! Flutter did not generate app\windows\CMakeLists.txt.
        echo     Check the message above, then run:  flutter doctor -v
        popd
        goto :fail
    )
)
copy /y windows\midas.ico windows\runner\resources\app_icon.ico >nul
rem Clear stale package-resolution state (old paths break the compile after
rem the project folder moves or the machine-wide cache changes).
if exist .dart_tool rmdir /s /q .dart_tool
if exist build rmdir /s /q build
if exist .flutter-plugins del /q .flutter-plugins
if exist .flutter-plugins-dependencies del /q .flutter-plugins-dependencies
call "%FLUTTER_CMD%" pub get || (popd & goto :fail)
call "%FLUTTER_CMD%" build windows --release || (popd & goto :fail)
if not exist build\windows\x64\runner\Release\midas.exe (
    echo   ! The Flutter release build produced no executable.
    popd
    goto :fail
)
popd
echo   - Flutter app built.

rem ---------- [7/8] assemble ----------
echo [7/8] Assembling dist\Midas ...
if exist dist\Midas rmdir /s /q dist\Midas || goto :fail
mkdir dist\Midas\engine dist\Midas\vendor dist\Midas\data || goto :fail
xcopy /e /i /q app\build\windows\x64\runner\Release\* dist\Midas\ >nul || goto :fail
copy /y engine\dist\midas-engine.exe dist\Midas\engine\ >nul || goto :fail
xcopy /e /i /q vendor dist\Midas\vendor >nul
if exist dist\Midas\midas.exe ren dist\Midas\midas.exe Midas.exe

rem ---------- [8/8] zip package ----------
echo [8/8] Zipping the release package...
if exist dist\Midas-win64.zip del dist\Midas-win64.zip
tar -a -c -f dist\Midas-win64.zip -C dist Midas || goto :fail
echo   - Created dist\Midas-win64.zip

echo.
echo  ============================================
echo    DONE. Your golden build is ready:
echo    dist\Midas\Midas.exe
echo    dist\Midas-win64.zip  ^(send this file to your users^)
echo  ============================================
echo.
pause
exit /b 0

:fail
echo.
echo  ******************************************************
echo   Build failed. Read the message above, fix the issue,
echo   and run build.bat again - completed steps are reused.
echo  ******************************************************
pause
exit /b 1
