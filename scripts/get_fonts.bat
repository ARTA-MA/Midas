@echo off
rem Downloads the bundled fonts (open-license) into app\assets\fonts.
rem Called by build.bat and run_dev.bat. Safe to re-run; skips existing files.
setlocal
set "FONTS_DIR=%~dp0..\app\assets\fonts"
if not exist "%FONTS_DIR%" mkdir "%FONTS_DIR%"

if exist "%FONTS_DIR%\Cormorant[wght].ttf" goto :manrope
echo   - Downloading Cormorant (display font)...
curl.exe -fL -sS -o "%FONTS_DIR%\Cormorant[wght].ttf.tmp" "https://github.com/google/fonts/raw/main/ofl/cormorant/Cormorant%%5Bwght%%5D.ttf"
if errorlevel 1 goto :fail
move /y "%FONTS_DIR%\Cormorant[wght].ttf.tmp" "%FONTS_DIR%\Cormorant[wght].ttf" >nul
if errorlevel 1 goto :fail

:manrope
if exist "%FONTS_DIR%\Manrope[wght].ttf" goto :done
echo   - Downloading Manrope (UI font)...
curl.exe -fL -sS -o "%FONTS_DIR%\Manrope[wght].ttf.tmp" "https://github.com/google/fonts/raw/main/ofl/manrope/Manrope%%5Bwght%%5D.ttf"
if errorlevel 1 goto :fail
move /y "%FONTS_DIR%\Manrope[wght].ttf.tmp" "%FONTS_DIR%\Manrope[wght].ttf" >nul
if errorlevel 1 goto :fail

:done
echo   - Fonts ready.
exit /b 0

:fail
if exist "%FONTS_DIR%\Cormorant[wght].ttf.tmp" del "%FONTS_DIR%\Cormorant[wght].ttf.tmp"
if exist "%FONTS_DIR%\Manrope[wght].ttf.tmp" del "%FONTS_DIR%\Manrope[wght].ttf.tmp"
echo   ! Font download failed. Check your internet connection and retry.
exit /b 1
