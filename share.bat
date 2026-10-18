@echo off
rem Share Pitwall with anyone via a public link. Everything runs in the background;
rem this window can be closed once the link is shown. Use stop-sharing.bat to stop.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0share.ps1"
echo.
pause
