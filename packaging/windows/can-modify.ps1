param([Parameter(Mandatory=$true)][string]$AppRoot)
# Read only the public health response and non-secret launch metadata. Never open
# a ledger, session, token, key or receipt while installing/uninstalling code.
$ErrorActionPreference = 'Stop'
try { $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/health' -TimeoutSec 3 }
catch {
    try {
        $pending = @(Get-NetTCPConnection -State Listen -LocalPort 8766 -ErrorAction SilentlyContinue)
        if ($pending.Count) { exit 2 }
        exit 0
    } catch { exit 2 }
}
if ($health.app -ne 'receipt-ledger') { exit 0 }
try {
    $recordPath = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger\runtime\server.json'
    if (-not (Test-Path -LiteralPath $recordPath)) { exit 2 }
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    if (-not $record.source -or -not $record.pid) { exit 2 }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8766 -ErrorAction Stop)
    if ($listeners.Count -ne 1 -or $listeners[0].OwningProcess -ne [int]$record.pid) { exit 2 }
    $target = [System.IO.Path]::GetFullPath($AppRoot).TrimEnd('\')
    $source = [System.IO.Path]::GetFullPath([string]$record.source).TrimEnd('\')
    if ($target -ieq $source) { exit 2 }
    exit 0
} catch { exit 2 }
