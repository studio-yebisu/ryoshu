$ErrorActionPreference = 'Stop'
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$recordPath = Join-Path $taskRoot 'runtime\server.json'
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Host 'No app process recorded.'; exit 0 }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$appProcess = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$record.pid) -ErrorAction SilentlyContinue
if (-not $appProcess) { Write-Host 'The app is already stopped.'; exit 0 }
if (($appProcess.ExecutablePath -ine $record.python) -or ($appProcess.CommandLine -notmatch '-m\s+receipt_ledger\b')) { throw 'The recorded process no longer matches this application. No process was stopped.' }
$listener = Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue
if ($listener -and $listener.OwningProcess -ne [int]$record.pid) {
    # The Windows venv launcher can spawn a child base-Python process.
    $serverChild = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$listener.OwningProcess) -ErrorAction SilentlyContinue
    if (-not $serverChild -or $serverChild.ParentProcessId -ne [int]$record.pid -or $serverChild.CommandLine -notmatch '-m\s+receipt_ledger\b') { throw 'The listener belongs to a different process. No process was stopped.' }
    Stop-Process -Id ([int]$serverChild.ProcessId)
}
if (Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue) { Stop-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue }
Write-Host 'ReceiptLedger stopped. Local records and Ollama are preserved.'
