$ErrorActionPreference = 'Stop'
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$runtimeDir = Join-Path $taskRoot 'runtime'
$recordPath = Join-Path $runtimeDir 'server.json'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
. (Join-Path $PSScriptRoot 'scripts\launcher-process.ps1')
$startupLock = Open-RyoshuStartupLock $runtimeDir
try {
    $record = $null
    if (Test-Path -LiteralPath $recordPath) { try { $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json } catch {} }
    $servers = @(Get-RyoshuListener -PythonExe $pythonExe -Record $record)
    $targets = @($servers | Select-Object -ExpandProperty ProcessId)
    if ($record) {
        $recordProcess = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$record.pid) -ErrorAction SilentlyContinue
        if ($recordProcess -and $recordProcess.ExecutablePath -ieq $record.python -and $recordProcess.CommandLine -match '-m\s+receipt_ledger\b') { $targets += [int]$recordProcess.ProcessId }
    }
    # Ownership of every listener is verified before any process is stopped.
    foreach ($targetId in @($targets | Select-Object -Unique)) { Stop-Process -Id ([int]$targetId) -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $recordPath) { Remove-Item -LiteralPath $recordPath -Force -ErrorAction SilentlyContinue }
    if ($targets.Count) { Write-Host 'Ryoshuを終了しました。帳簿・原本・Ollamaはそのまま保存されています。' }
    else { Write-Host 'Ryoshuはすでに終了しています。帳簿・原本は保存されています。' }
} finally { $startupLock.Dispose() }
