# Share Pitwall publicly: starts the server and a Cloudflare quick tunnel in the
# background (no windows to close by accident), checks the public link works,
# copies it to the clipboard and saves it in PUBLIC-LINK.txt.
# Stop everything with stop-sharing.bat.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$py = Join-Path $root '.venv\Scripts\python.exe'
$cf = Join-Path $root 'tools\cloudflared.exe'
$log = Join-Path $root 'share-log.txt'
$linkFile = Join-Path $root 'PUBLIC-LINK.txt'

function Test-Url($u) {
    try { (Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 $u).StatusCode -eq 200 } catch { $false }
}

if (-not (Test-Path $cf)) {
    New-Item -ItemType Directory -Force (Join-Path $root 'tools') | Out-Null
    Write-Host 'Downloading cloudflared (Cloudflare''s official tunnel tool)...'
    Invoke-WebRequest -UseBasicParsing -OutFile $cf 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe'
}

# 1. website server (background, hidden)
if (-not (Test-Url 'http://localhost:8000/api/health')) {
    if (-not (Test-Path $py)) { throw 'Python environment missing - run run.bat once first.' }
    Write-Host 'Starting the Pitwall server in the background...'
    Start-Process -FilePath $py -ArgumentList '-m uvicorn backend.main:app --port 8000 --reload --reload-dir backend' -WorkingDirectory $root -WindowStyle Hidden
    for ($i = 0; $i -lt 90 -and -not (Test-Url 'http://localhost:8000/api/health'); $i++) { Start-Sleep 1 }
    if (-not (Test-Url 'http://localhost:8000/api/health')) { throw 'The server did not start - double-click run.bat to see the error.' }
}
Write-Host 'Server is running.'

# 2. public tunnel (background, hidden) - replaces any old one
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process cloudflared -ErrorAction SilentlyContinue | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue
Remove-Item $linkFile -ErrorAction SilentlyContinue
# a fresh log every run, so an old (possibly still locked) log can never hand back a dead link
Get-ChildItem $root -Filter 'share-log*.txt' | Remove-Item -ErrorAction SilentlyContinue
$log = Join-Path $root ("share-log-{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
Write-Host 'Creating the public link...'
Start-Process -FilePath $cf -ArgumentList "tunnel --no-autoupdate --logfile `"$log`" --url http://localhost:8000" -WorkingDirectory $root -WindowStyle Hidden

$pat = 'https://[a-z0-9-]+\.trycloudflare\.com'
$u = $null
for ($i = 0; $i -lt 60 -and -not $u; $i++) {
    Start-Sleep 1
    if (Test-Path $log) {
        $m = Select-String -Path $log -Pattern $pat | Select-Object -First 1
        if ($m) { $u = $m.Matches[0].Value }
    }
}
if (-not $u) { throw 'Cloudflare did not hand out a link - see the share-log file.' }

# 3. wait until the link really works from the internet
Write-Host "Checking $u ..."
$ok = $false
for ($i = 0; $i -lt 45 -and -not $ok; $i++) { Start-Sleep 2; $ok = Test-Url "$u/api/health" }

Set-Content -Path $linkFile -Value $u
try { Set-Clipboard -Value $u } catch {}
Write-Host ''
Write-Host '  ======================================================' -ForegroundColor Red
Write-Host "  SHARE THIS LINK:  $u" -ForegroundColor Green
if ($ok) { Write-Host '  Checked: it works from the internet.' -ForegroundColor Green }
else { Write-Host '  (not reachable yet - give it a minute)' -ForegroundColor Yellow }
Write-Host '  Copied to your clipboard and saved in PUBLIC-LINK.txt.' -ForegroundColor Gray
Write-Host '  It keeps running in the background - you can close this window.' -ForegroundColor Gray
Write-Host '  To stop sharing: double-click stop-sharing.bat' -ForegroundColor Gray
Write-Host '  ======================================================' -ForegroundColor Red
Add-Content -Path $log -Value "SHARE_READY $u ok=$ok"
Start-Process $u
