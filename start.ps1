param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
$appUrl = 'http://127.0.0.1:8766'
if (-not (Test-Path -LiteralPath $pythonExe)) { & (Join-Path $PSScriptRoot 'setup.ps1') }
try { $runningApp = Invoke-RestMethod -Uri ($appUrl + '/api/health') -TimeoutSec 4 } catch { $runningApp = $null }
if ($runningApp -and $runningApp.app -eq 'receipt-ledger') {
    if (-not $NoBrowser) { Start-Process $appUrl }
    Write-Host ('Already running: ' + $appUrl)
    exit 0
}
if (Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8766 is used by another application.' }
try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 | Out-Null }
catch {
    $ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollamaCommand) { Start-Process -FilePath $ollamaCommand.Source -ArgumentList @('serve') -WindowStyle Hidden | Out-Null }
}
$env:PYTHONUTF8 = '1'
$env:RECEIPT_LEDGER_PORT = '8766'
$env:RECEIPT_LEDGER_DATA = Join-Path $taskRoot 'data'
New-Item -ItemType Directory -Path (Join-Path $taskRoot 'runtime') -Force | Out-Null
$serverProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','receipt_ledger') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRoot 'runtime\stdout.log') -RedirectStandardError (Join-Path $taskRoot 'runtime\stderr.log')
@{pid=$serverProcess.Id; python=$pythonExe; source=$PSScriptRoot; url=$appUrl} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $taskRoot 'runtime\server.json') -Encoding UTF8
$appReady = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $runningApp = Invoke-RestMethod -Uri ($appUrl + '/api/health') -TimeoutSec 4
        if ($runningApp.app -eq 'receipt-ledger') { $appReady = $true; break }
    } catch {}
    if ($serverProcess.HasExited) { break }
}
if (-not $appReady) { throw ('App failed to start. See ' + (Join-Path $taskRoot 'runtime\stderr.log')) }
if (-not $NoBrowser) { Start-Process $appUrl }
Write-Host ('Ready: ' + $appUrl)
