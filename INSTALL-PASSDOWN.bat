@echo off
setlocal enabledelayedexpansion
title IND3 — Shift Passdown Server Installer

:: ============================================================
::  IND3 Shift Passdown Generator — Shop PC Installer
::
::  Installs the server to C:\IND3Passdown and registers it
::  in the Windows Startup Folder so it auto-starts on login.
::  NO admin rights needed.
::
::  HOW TO USE:
::    1. Unzip the transfer package anywhere (Desktop is fine)
::    2. Double-click this file — NO "Run as administrator"
::    3. Server starts immediately; note the URL printed at end
:: ============================================================

echo.
echo  =========================================================
echo   IND3  ^|  Shift Passdown Server Installer
echo  =========================================================
echo.

:: ── Config ────────────────────────────────────────────────
set INSTALL_DIR=C:\IND3Passdown
set PORT=8766
set SOURCE=%~dp0
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup

echo  Source folder:  %SOURCE%
echo  Install folder: %INSTALL_DIR%
echo  Startup folder: %STARTUP%
echo.

:: ── Step 1: Create install directory ──────────────────────
echo  [1/5] Creating %INSTALL_DIR%...
if not exist "%INSTALL_DIR%" (
    mkdir "%INSTALL_DIR%"
    if %errorlevel% neq 0 (
        echo  [ERROR] Cannot create %INSTALL_DIR%
        echo          Try changing INSTALL_DIR at the top of this file.
        pause
        exit /b 1
    )
)
echo  [OK] Folder ready.
echo.

:: ── Step 2: Copy all project files (including .venv) ──────
echo  [2/5] Copying project files...
echo        (The Python environment is large — may take 1-3 min)
echo.
robocopy "%SOURCE%." "%INSTALL_DIR%" /E ^
  /XD "__pycache__" ".git" "node_modules" ^
  /XF "*.log" "*.pyc" ^
  /NFL /NDL /NJH /NJS /nc /ns /np

:: robocopy exits 1 = "files copied OK" — 8+ is a real error
if %errorlevel% geq 8 (
    echo  [ERROR] File copy failed!
    echo          Make sure you have write access to C:\
    pause
    exit /b 1
)
echo  [OK] Files copied.
echo.

:: ── Step 3: Write the restart-loop launcher ───────────────
echo  [3/5] Creating server launcher...
(
    echo @echo off
    echo cd /d "%INSTALL_DIR%"
    echo title IND3 Shift Passdown Server ^(port %PORT%^) — Keep this running!
    echo color 0A
    echo echo.
    echo echo  ================================================
    echo echo   IND3 Shift Passdown Server
    echo echo   Running on port %PORT%
    echo echo   DO NOT CLOSE THIS WINDOW
    echo echo  ================================================
    echo echo.
    echo.
    echo :loop
    echo echo [%%date%% %%time%%] Starting server...
    echo "%INSTALL_DIR%\.venv\Scripts\uvicorn.exe" main:app --host 0.0.0.0 --port %PORT% --log-level warning
    echo echo.
    echo echo [%%date%% %%time%%] Server stopped. Restarting in 5 seconds...
    echo echo  ^(Close this window if you want it to stay stopped^)
    echo timeout /t 5 /nobreak
    echo goto loop
) > "%INSTALL_DIR%\run-server.bat"
echo  [OK] Launcher created.
echo.

:: ── Step 4: Add to Windows Startup Folder (no admin) ──────
echo  [4/5] Registering auto-start on login...
(
    echo Set sh = CreateObject^("WScript.Shell"^)
    echo sh.CurrentDirectory = "%INSTALL_DIR%"
    echo sh.Run """%INSTALL_DIR%\run-server.bat""", 2, False
) > "%STARTUP%\IND3PassdownServer.vbs"

if %errorlevel% neq 0 (
    echo  [ERROR] Could not write to Startup folder!
    pause
    exit /b 1
)
echo  [OK] Startup entry: %STARTUP%\IND3PassdownServer.vbs
echo.

:: ── Step 5: Launch right now ──────────────────────────────
echo  [5/5] Starting server now...
start "" /min "%INSTALL_DIR%\run-server.bat"
timeout /t 5 /nobreak >nul

:: Quick health check
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $r = Invoke-WebRequest -Uri 'http://localhost:%PORT%/' -TimeoutSec 5 -UseBasicParsing; Write-Host '  [OK] Server is UP!' } catch { Write-Host '  [WAIT] Server still warming up — give it 10 more seconds.' }"

echo.
echo  =========================================================
echo   INSTALL COMPLETE!
echo  =========================================================
echo.
echo  The server is now:
echo    - Running RIGHT NOW (minimized in taskbar)
echo    - AUTO-STARTS every time someone logs into this PC
echo    - Auto-restarts itself if it ever crashes
echo.
echo  ── This PC's network address ───────────────────────────
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /R /C:"IPv4 Address"') do (
    set PCIP=%%a
    set PCIP=!PCIP: =!
    echo    Passdown App:  http://!PCIP!:%PORT%/
    echo    Lead View:     http://!PCIP!:%PORT%/lead
    echo    Tech Login:    http://!PCIP!:%PORT%/tech
    echo.
)
echo  ── To UNINSTALL ────────────────────────────────────────
echo.
echo    Delete: %STARTUP%\IND3PassdownServer.vbs
echo    Delete: %INSTALL_DIR%\  (optional — keeps your database)
echo.
pause
