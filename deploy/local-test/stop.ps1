<#
Stops the temporary public test started by deploy\local-test\start.ps1 (tunnel, front door and
test API). The development database keeps running: python backend\scripts\dev_db.py --stop.
#>
param([switch]$Quiet)
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Run = Join-Path $Root ".tools\local-test"
$stopped = 0
foreach ($name in "cloudflared", "caddy", "api") {
    $pidFile = Join-Path $Run "$name.pid"
    if (-not (Test-Path $pidFile)) { continue }
    $id = [int](Get-Content $pidFile)
    $proc = Get-Process -Id $id -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        $stopped++
    }
    Remove-Item $pidFile -ErrorAction SilentlyContinue
}
if (-not $Quiet) {
    if ($stopped) { Write-Host "Stopped the public test ($stopped programs)." }
    else { Write-Host "The public test was not running." }
}
