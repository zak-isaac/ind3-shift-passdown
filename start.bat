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

echo Starting server on http://localhost:8765
echo Press Ctrl+C to stop.
echo.

uv run uvicorn main:app --host 0.0.0.0 --port 8765

pause
