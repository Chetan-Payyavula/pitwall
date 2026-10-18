@echo off
rem (only python.exe processes are matched - this PowerShell's own command line contains the same words)
rem Stops the public link and the background Pitwall server (including its auto-reload worker).
taskkill /im cloudflared.exe /f >nul 2>&1
powershell -NoProfile -Command "$all = Get-CimInstance Win32_Process; $ids = @($all | Where-Object { $_.Name -eq 'python.exe' -and $_.ProcessId -ne $PID -and $_.CommandLine -like '*uvicorn*backend.main*' } | ForEach-Object { $_.ProcessId }); $kids = @($all | Where-Object { $ids -contains $_.ParentProcessId } | ForEach-Object { $_.ProcessId }); $port = @(Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { $_.OwningProcess }); ($ids + $kids + $port) | Sort-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }"
del "%~dp0PUBLIC-LINK.txt" >nul 2>&1
echo Sharing stopped - the public link no longer works.
timeout /t 4 >nul
