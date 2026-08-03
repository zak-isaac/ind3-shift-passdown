@echo off
:: INSTALL-AS-SERVICE.bat
:: Double-click this to install the passdown server as a persistent Windows service.
:: Will auto-elevate (UAC prompt) - click YES when it asks.

net session >nul 2>&1
if %errorLevel% == 0 goto :elevated

:: Not elevated - re-launch self as admin
echo Requesting administrator privileges...
powershell -Command "Start-Process cmd -ArgumentList '/c \"%~f0\"' -Verb RunAs"
exit /b

:elevated
echo.
echo  Installing IND3 Passdown Server as a persistent system service...
echo.

powershell -ExecutionPolicy Bypass -File "%~dp0install_service.ps1"

echo.
echo  Done! Press any key to close.
pause >nul
