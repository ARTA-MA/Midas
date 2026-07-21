@echo off
setlocal
title Midas - publish to GitHub
cd /d "%~dp0"

rem =====================================================================
rem  Pushes the full Midas source to YOUR empty GitHub repository and
rem  tags v1.1.0, ready for a release. One-time prerequisites:
rem    1. A GitHub account          https://github.com/signup
rem    2. A NEW, EMPTY repository   https://github.com/new
rem       (any name, e.g. "midas" - do NOT tick "Add a README")
rem    3. Git                       winget install --id Git.Git
rem =====================================================================

echo.
echo === Publish Midas to GitHub ===
echo.

where git >nul 2>nul || (
    echo Git was not found. Install it first:
    echo     winget install --id Git.Git
    echo then re-run this script.
    exit /b 1
)

set /p REPO_URL=Paste your EMPTY GitHub repo URL (e.g. https://github.com/YOU/midas.git): 
if "%REPO_URL%"=="" (
    echo No URL given.
    exit /b 1
)

if not exist ".git" git init -b main
rem a local identity is required for committing; set one if missing
git config user.email >nul 2>nul || git config user.email "midas@local"
git config user.name  >nul 2>nul || git config user.name  "Midas"

git add -A || goto :fail
git commit -m "Midas v1.1.0 - full source" >nul 2>nul
git tag -f v1.1.0
git remote remove origin >nul 2>nul
git remote add origin "%REPO_URL%" || goto :fail
echo Pushing... (if a browser window opens, just sign in to GitHub)
git push -u origin main --tags || (
    echo.
    echo Push failed. Usual causes:
    echo   - wrong repo URL, or the repo is not empty
    echo   - login window was closed: run this script again and sign in
    goto :fail
)

set "REPO_WEB=%REPO_URL:.git=%"
echo.
echo =====================================================
echo  Source pushed and tagged v1.1.0!
echo.
echo  Final step - publish the release (the only manual part):
echo    1. Open  %REPO_WEB%/releases/new?tag=v1.1.0
echo    2. Title:  Midas v1.1.0
echo    3. Paste the text from  docs\RELEASE_NOTES_v1.1.0.md
echo    4. Attach  dist\Midas-Setup.msi   (made by build_msi.bat)
echo    5. Click "Publish release"
echo =====================================================
exit /b 0

:fail
echo.
echo PUBLISH FAILED - see the messages above.
exit /b 1
