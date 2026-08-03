@echo off
:: IND3 Passdown Server — launcher
:: Runs as SYSTEM via Task Scheduler; uses fully-qualified paths.

set PROJECT=C:\Users\ins0002\Documents\puppy_workspace\IND3-Passdown-Server
set PYTHON=%PROJECT%\.venv\Scripts\python.exe
set LOG=%PROJECT%\server.log

cd /d "%PROJECT%"

:loop
echo [%DATE% %TIME%] Starting passdown server... >> "%LOG%"
"%PYTHON%" -m uvicorn main:app --host 0.0.0.0 --port 8765 >> "%LOG%" 2>&1
echo [%DATE% %TIME%] Server exited - restarting in 5s >> "%LOG%"
ping -n 6 127.0.0.1 >nul
goto loop
