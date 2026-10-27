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
    $targets = @($targets | Select-Object -Unique)
    # Ownership of every listener is verified before any process is stopped.
    foreach ($targetId in $targets) { Stop-Process -Id ([int]$targetId) -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $recordPath) { Remove-Item -LiteralPath $recordPath -Force -ErrorAction SilentlyContinue }
    # The app's Job Object normally ends its llama-server with it. As a backstop, stop only children
    # proven by an owner record (pid + creation time + exe under Ryoshu's llama folder) whose app is
    # being stopped here or is gone. Records of another live app instance are left untouched.
    foreach ($owned in @(Get-RyoshuOwnedLlamaChildren -TaskRoot $taskRoot -StoppingAppIds $targets)) {
        if ($owned.AppAlive -and -not $owned.AppBeingStopped) { continue }
        if ($owned.ChildMatches) { Stop-Process -Id $owned.ChildId -ErrorAction SilentlyContinue; $targets += $owned.ChildId }
        Remove-Item -LiteralPath $owned.Record -Force -ErrorAction SilentlyContinue
    }
    if ($targets.Count) { Write-Host 'RyoshuとRyoshu専用のAI処理を終了しました。帳簿・原本・モデル・実行環境は削除していません。他のアプリは停止していません。' }
    else { Write-Host 'Ryoshuは起動していません。帳簿と原本はそのままです。' }
} finally { $startupLock.Dispose() }
