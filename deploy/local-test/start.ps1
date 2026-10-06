<#
Starts a TEMPORARY public test of the whole system from this PC (no server, no card needed):
the database, the API, the ordering page and a free Cloudflare quick tunnel. Prints the public
address, the table PIN and the table links, and writes printable QR cards.

    powershell -ExecutionPolicy Bypass -File deploy\local-test\start.ps1
    powershell -ExecutionPolicy Bypass -File deploy\local-test\start.ps1 -Code dhaba-test -Apk

  -Code  restaurant code to print the PIN and QR cards for (default: dhaba-test)
  -Apk   also build the staff app for phones (staff-app-test.apk), set to this address

Stop it with deploy\local-test\stop.ps1. The address changes on every start. Cloudflare deletes a
quick tunnel when this PC sleeps or loses its connection: then simply run this script again.
For a real restaurant use a server instead (docs/07-deployment.md).
#>
param(
    [string]$Code = "dhaba-test",
    [switch]$Apk
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Backend = Join-Path $Root "backend"
$Web = Join-Path $Root "customer-web"
$Py = Join-Path $Backend ".venv\Scripts\python.exe"
$Tools = Join-Path $Root ".tools"
$Run = Join-Path $Tools "local-test"
$Caddy = Join-Path $Tools "caddy.exe"
$Cloudflared = Join-Path $Tools "cloudflared.exe"
$Cards = Join-Path $Root "demo-qr-cards.html"

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }

function Wait-Url($url, $seconds) {
    for ($i = 0; $i -lt $seconds; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) { return $true }
        } catch { }
        Start-Sleep -Seconds 1
    }
    return $false
}

if (-not (Test-Path $Py)) {
    throw "The backend is not set up yet: create backend\.venv first (see README.md)."
}
New-Item -ItemType Directory -Force $Run | Out-Null

# A previous run (or a lost tunnel): stop it first.
& (Join-Path $PSScriptRoot "stop.ps1") -Quiet

Step "Tools (downloaded once into .tools\)"
if (-not (Test-Path $Caddy)) {
    Write-Host "Downloading Caddy..."
    Invoke-WebRequest -UseBasicParsing -OutFile $Caddy "https://caddyserver.com/api/download?os=windows&arch=amd64"
}
if (-not (Test-Path $Cloudflared)) {
    Write-Host "Downloading cloudflared..."
    Invoke-WebRequest -UseBasicParsing -OutFile $Cloudflared "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
}
Write-Host "ok"

Step "Database"
Push-Location $Backend
try { & $Py scripts\dev_db.py; if ($LASTEXITCODE) { throw "the database did not start" } } finally { Pop-Location }

Step "Ordering page (build)"
Push-Location $Web
try { & npm run build --silent; if ($LASTEXITCODE) { throw "the ordering page did not build" } } finally { Pop-Location }

Step "Cloudflare quick tunnel"
$tunnelLog = Join-Path $Run "tunnel.log"
Remove-Item $tunnelLog -ErrorAction SilentlyContinue
$tunnel = Start-Process -FilePath $Cloudflared -PassThru -WindowStyle Hidden `
    -ArgumentList "tunnel --no-autoupdate --url http://127.0.0.1:8081" `
    -RedirectStandardError $tunnelLog -RedirectStandardOutput (Join-Path $Run "tunnel.out")
$tunnel.Id | Set-Content (Join-Path $Run "cloudflared.pid")
$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $tunnelLog) {
        $m = Select-String -Path $tunnelLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value }
    }
}
if (-not $url) { throw "Cloudflare did not give an address (see $tunnelLog). Check the Internet connection." }
$url | Set-Content (Join-Path $Run "url.txt")
Write-Host $url

Step "API (port 8040)"
# The tunnel address is where QR links point and the only site allowed to send customer orders.
$env:PUBLIC_ORDER_BASE_URL = $url
$env:PUBLIC_WEB_ORIGINS = $url
$env:PASSWORD_RESET_URL = "$url/reset-password"
$env:TRUSTED_PROXIES = "127.0.0.1/32,::1/128"
$env:REGISTRATION_MODE = "closed"   # the test is public: nobody may create restaurants
$api = Start-Process -FilePath $Py -PassThru -WindowStyle Hidden -WorkingDirectory $Backend `
    -ArgumentList "-m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8040 --ws-max-size 65536 --no-proxy-headers --no-server-header" `
    -RedirectStandardOutput (Join-Path $Run "api.out") -RedirectStandardError (Join-Path $Run "api.log")
$api.Id | Set-Content (Join-Path $Run "api.pid")
if (-not (Wait-Url "http://127.0.0.1:8040/healthz" 60)) { throw "the API did not start (see $Run\api.log)" }
Write-Host "ok"

Step "Front door (Caddy, port 8081)"
$env:CUSTOMER_ROOT = (Join-Path $Web "dist") -replace "\\", "/"
$env:XDG_DATA_HOME = $Run
$env:XDG_CONFIG_HOME = $Run
$caddyProc = Start-Process -FilePath $Caddy -PassThru -WindowStyle Hidden `
    -ArgumentList "run --config `"$(Join-Path $PSScriptRoot 'Caddyfile')`" --adapter caddyfile" `
    -RedirectStandardOutput (Join-Path $Run "caddy.out") -RedirectStandardError (Join-Path $Run "caddy.log")
$caddyProc.Id | Set-Content (Join-Path $Run "caddy.pid")
if (-not (Wait-Url "http://127.0.0.1:8081/healthz" 30)) { throw "Caddy did not start (see $Run\caddy.log)" }
Write-Host "ok"

Step "Reachable from the Internet?"
if (Wait-Url "$url/healthz" 90) { Write-Host "yes" -ForegroundColor Green }
else { Write-Host "Not yet - try the address again in a minute." -ForegroundColor Yellow }

Step "Restaurant '$Code'"
Push-Location $Backend
try {
    & $Py -m app.cli show-pin --code $Code
    & $Py -m app.cli qr-sheet --code $Code --out $Cards
} finally { Pop-Location }

if ($Apk) {
    Step "Staff app for phones (a few minutes)"
    Push-Location (Join-Path $Root "staff_app")
    try {
        & flutter build apk --release "--dart-define=API_BASE_URL=$url"
        if ($LASTEXITCODE) { throw "the app did not build" }
        Copy-Item "build\app\outputs\flutter-apk\app-release.apk" (Join-Path $Root "staff-app-test.apk") -Force
        Write-Host "staff-app-test.apk is ready: copy it to the phone and install it."
    } finally { Pop-Location }
}

Write-Host ""
Write-Host "Test is running at: $url" -ForegroundColor Green
Write-Host "  QR cards:   open demo-qr-cards.html in Chrome and scan one with the phone"
Write-Host "  Staff app:  cd staff_app; flutter run -d windows --dart-define=API_BASE_URL=http://127.0.0.1:8081"
Write-Host "  Keep this PC awake. If the link stops working, run this script again."
Write-Host "  Stop:       powershell -ExecutionPolicy Bypass -File deploy\local-test\stop.ps1"
