@echo off
setlocal
title Midas MSI builder
cd /d "%~dp0"

rem =====================================================================
rem  Midas .msi builder - downloads everything it needs, then builds.
rem
rem  Result: dist\Midas-Setup.msi  (single file; any Windows user can
rem  run it to install Midas system-wide with Start-menu/desktop
rem  shortcuts and a clean uninstall in "Apps & features").
rem
rem  Dependencies (fetched automatically, no manual installs):
rem    - the portable app build      -> build.bat (downloads its own deps)
rem    - WiX Toolset 3.11 binaries   -> downloaded into tools\wix
rem =====================================================================

set "PRODUCT_VERSION=1.2.0"
set "WIX_DIR=tools\wix"
set "WIX_ZIP_URL=https://github.com/wixtoolset/wix3/releases/download/wix3112rtm/wix311-binaries.zip"

echo.
echo === Midas .msi builder ===
echo.

rem [1/4] portable app build --------------------------------------------
if exist "dist\Midas\Midas.exe" (
    echo [1/4] Using the existing dist\Midas build.
) else (
    echo [1/4] dist\Midas not found - running build.bat first...
    call build.bat || goto :fail
    if not exist "dist\Midas\Midas.exe" (
        echo build.bat did not produce dist\Midas\Midas.exe
        goto :fail
    )
)

rem [2/4] WiX toolset (portable, ~35 MB, downloaded once) ---------------
if exist "%WIX_DIR%\candle.exe" (
    echo [2/4] Using the WiX toolset already in %WIX_DIR%.
    goto :wix_ok
)
echo [2/4] Downloading the WiX toolset...
if not exist "%WIX_DIR%" mkdir "%WIX_DIR%"
curl -L --fail --retry 3 -o "%WIX_DIR%\wix311.zip" "%WIX_ZIP_URL%" || (
    echo   curl failed - trying PowerShell...
    powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest '%WIX_ZIP_URL%' -OutFile '%WIX_DIR%\wix311.zip'" || goto :fail
)
echo   Extracting...
tar -xf "%WIX_DIR%\wix311.zip" -C "%WIX_DIR%" 2>nul || (
    powershell -NoProfile -Command "Expand-Archive -Force '%WIX_DIR%\wix311.zip' '%WIX_DIR%'" || goto :fail
)
del "%WIX_DIR%\wix311.zip" >nul 2>nul
if not exist "%WIX_DIR%\candle.exe" (
    echo   WiX extraction failed - candle.exe is missing.
    goto :fail
)
:wix_ok
echo   - WiX ready.

rem [3/4] harvest the app files into a WiX fragment ---------------------
echo [3/4] Harvesting dist\Midas file list...
"%WIX_DIR%\heat.exe" dir "dist\Midas" -nologo ^
    -cg MidasFiles -dr INSTALLFOLDER -gg -g1 -srd -sfrag -sreg -scom ^
    -var var.SourceDir -out "installer\_files.generated.wxs" || goto :fail

rem [4/4] compile + link the MSI ----------------------------------------
echo [4/4] Building dist\Midas-Setup.msi ...
if not exist "build\msi" mkdir "build\msi"
"%WIX_DIR%\candle.exe" -nologo -arch x64 ^
    -dSourceDir="dist\Midas" -dProductVersion=%PRODUCT_VERSION% ^
    "installer\Midas.wxs" "installer\_files.generated.wxs" ^
    -o "build\msi\\" || goto :fail
"%WIX_DIR%\light.exe" -nologo -sval -spdb ^
    "build\msi\Midas.wixobj" "build\msi\_files.generated.wixobj" ^
    -o "dist\Midas-Setup.msi" || goto :fail

echo.
echo =====================================================
echo  Done:  dist\Midas-Setup.msi
echo.
echo  Single-file installer for any Windows user:
echo    - installs to Program Files (per-machine)
echo    - Start-menu + desktop shortcuts
echo    - uninstall from Settings ^> Apps ^& features
echo    - user data stays in each user's %%APPDATA%%\Midas
echo.
echo  Note: the MSI is unsigned, so SmartScreen may show
echo  "unknown publisher" on first run. Users can click
echo  "More info ^> Run anyway"; a code-signing certificate
echo  removes the warning entirely.
echo =====================================================
exit /b 0

:fail
echo.
echo BUILD FAILED - see the messages above.
exit /b 1
