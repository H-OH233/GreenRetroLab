@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title GreenRetroLab - installer
echo ======================================================================
echo  GreenRetroLab one-click installer
echo ----------------------------------------------------------------------
echo  This will:
echo    1) locate (or download) Miniconda
echo    2) create two conda envs: molprop (Python 3.10) and aizynthfinder (3.11)
echo    3) install all dependencies from a fast mirror
echo    4) run a self-check (doctor)
echo.
echo  On a fresh machine this usually takes 15-40 minutes.
echo ======================================================================
echo.

where conda >nul 2>nul
if errorlevel 1 (
  echo [INFO] conda was not found on PATH.
  echo        Miniconda can be downloaded and installed automatically ~130MB.
  echo.
  set /p ANS=Install Miniconda automatically now? [Y/N]:
  if /i "!ANS!"=="Y" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1" -InstallMiniconda
  ) else (
    echo.
    echo Please install Miniconda manually from
    echo   https://docs.conda.io/en/latest/miniconda.html
    echo then run this installer again.
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
  )
) else (
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1"
)

echo.
echo Done. If the self-check above is all green, go back and double-click start.bat
if not "%GREENRETRO_NOPAUSE%"=="1" pause
