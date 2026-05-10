@echo off
title Shift Passdown Generator
cd /d "%~dp0"

echo ============================================================
echo   WALMART Shift Passdown Generator
echo ============================================================
echo.

REM ── Optional email config ──────────────────────────────────
REM Uncomment and fill these in to enable End-Shift email sending:
REM set PASSDOWN_EMAIL_FROM=you@wal-mart.com
REM set PASSDOWN_EMAIL_PASS=your-app-password
REM set PASSDOWN_EMAIL_TO=supervisor@wal-mart.com

echo Starting server on http://localhost:8765 and http://localhost:8003
echo Press Ctrl+C to stop.
echo.

REM Start port 8003 in the background, then run 8765 in the foreground
start "Passdown-8003" uv run uvicorn main:app --host 0.0.0.0 --port 8003

uv run uvicorn main:app --host 0.0.0.0 --port 8765

pause
