@echo off
rem ============================================================
rem  MIDAS - prerequisite bootstrapper
rem  Called by build.bat / run_dev.bat. Anything missing is
rem  downloaded as a PORTABLE copy into <repo>\tools\ - no admin
rem  rights, nothing installed system-wide. The only exception is
rem  the Visual Studio C++ Build Tools (Microsoft offers no
rem  portable version); we try winget, else show instructions.
rem
rem  On success, sets for the caller:  PYTHON_CMD  FLUTTER_CMD
rem ============================================================

set "TOOLS_DIR=%~dp0..\tools"
if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"

rem ------------------------------------------------ Python ----
set "PYTHON_CMD="
where python >nul 2>nul
if errorlevel 1 goto :py_local
for /f "delims=" %%v in ('python -c "import sys;print(1 if sys.version_info>=(3,10) else 0)" 2^>nul') do if "%%v"=="1" set "PYTHON_CMD=python"
if defined PYTHON_CMD goto :py_done

:py_local
if exist "%TOOLS_DIR%\python\tools\python.exe" set "PYTHON_CMD=%TOOLS_DIR%\python\tools\python.exe"
if defined PYTHON_CMD goto :py_done
echo    - Downloading portable Python 3.12 (about 35 MB, one time)...
curl.exe -fL -sS -o "%TOOLS_DIR%\python.zip" "https://www.nuget.org/api/v2/package/python/3.12.10" || goto :boot_fail
powershell -NoProfile -Command "Expand-Archive -Force '%TOOLS_DIR%\python.zip' '%TOOLS_DIR%\python'" || goto :boot_fail
del "%TOOLS_DIR%\python.zip"
set "PYTHON_CMD=%TOOLS_DIR%\python\tools\python.exe"
"%PYTHON_CMD%" -m ensurepip --upgrade >nul 2>nul
:py_done
echo    - Python ready.

rem ----------------------------------------------- Flutter ----
set "FLUTTER_CMD="
where flutter >nul 2>nul
if not errorlevel 1 set "FLUTTER_CMD=flutter"
if defined FLUTTER_CMD goto :flutter_done
if exist "%TOOLS_DIR%\flutter\bin\flutter.bat" goto :flutter_local

echo    - Downloading the Flutter SDK (about 1 GB, one time - grab a coffee)...
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; $r = Invoke-RestMethod 'https://storage.googleapis.com/flutter_infra_release/releases/releases_windows.json'; $s = $r.releases | Where-Object { $_.hash -eq $r.current_release.stable } | Select-Object -First 1; Invoke-WebRequest ($r.base_url + '/' + $s.archive) -OutFile '%TOOLS_DIR%\flutter.zip'" || goto :boot_fail
echo    - Unpacking the Flutter SDK...
tar -xf "%TOOLS_DIR%\flutter.zip" -C "%TOOLS_DIR%" || goto :boot_fail
del "%TOOLS_DIR%\flutter.zip"

:flutter_local
set "FLUTTER_CMD=%TOOLS_DIR%\flutter\bin\flutter.bat"
set "PATH=%TOOLS_DIR%\flutter\bin;%PATH%"

rem Flutter needs Git; fetch portable MinGit if missing.
where git >nul 2>nul
if not errorlevel 1 goto :flutter_done
if exist "%TOOLS_DIR%\git\cmd\git.exe" goto :git_path
echo    - Downloading portable Git (about 50 MB, one time)...
curl.exe -fL -sS -o "%TOOLS_DIR%\git.zip" "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/MinGit-2.47.1-64-bit.zip" || goto :boot_fail
powershell -NoProfile -Command "Expand-Archive -Force '%TOOLS_DIR%\git.zip' '%TOOLS_DIR%\git'" || goto :boot_fail
del "%TOOLS_DIR%\git.zip"
:git_path
set "PATH=%TOOLS_DIR%\git\cmd;%PATH%"
:flutter_done
echo    - Flutter ready.

rem ------------------ Visual C++ Build Tools (for Flutter) ----
set "VC_OK="
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" goto :vc_check_done
for /f "delims=" %%i in ('"%VSWHERE%" -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2^>nul') do set "VC_OK=1"
:vc_check_done
if defined VC_OK goto :boot_done
echo    ! Visual Studio C++ Build Tools not detected (needed to compile the Windows app).
where winget >nul 2>nul
if errorlevel 1 goto :vc_manual
echo    - Installing them via winget (large - this can take 10-30 minutes)...
winget install --id Microsoft.VisualStudio.2022.BuildTools -e --accept-package-agreements --accept-source-agreements --override "--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
if errorlevel 1 goto :vc_manual
goto :boot_done

:vc_manual
echo.
echo    Midas downloaded everything it could automatically, but the
echo    Visual Studio C++ Build Tools must be installed once by you:
echo      1. Download:  https://aka.ms/vs/17/release/vs_BuildTools.exe
echo      2. Run it and tick "Desktop development with C++"
echo      3. Re-run this script.
exit /b 1

:boot_done
exit /b 0

:boot_fail
echo    ! A download failed. Check your internet connection and re-run.
exit /b 1
