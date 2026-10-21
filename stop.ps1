$ErrorActionPreference = 'Stop'
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$recordPath = Join-Path $taskRoot 'runtime\server.json'
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Host 'Ryoshuの起動記録がありません。'; exit 0 }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$appProcess = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$record.pid) -ErrorAction SilentlyContinue
if (-not $appProcess) { Remove-Item -LiteralPath $recordPath -Force -ErrorAction SilentlyContinue; Write-Host 'Ryoshuはすでに終了しています。'; exit 0 }
if (($appProcess.ExecutablePath -ine $record.python) -or ($appProcess.CommandLine -notmatch '-m\s+receipt_ledger\b')) { throw '起動記録とアプリが一致しないため、終了操作を止めました。他のプロセスは停止していません。' }
$listeners = @(Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue)
$listenerIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
$serverChildren = @()
foreach ($listenerId in $listenerIds) {
    if ($listenerId -eq [int]$record.pid) { continue }
    # The Windows venv launcher can spawn a child base-Python process.
    $serverChild = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$listenerId) -ErrorAction SilentlyContinue
    if (-not $serverChild -or $serverChild.ParentProcessId -ne [int]$record.pid -or $serverChild.CommandLine -notmatch '-m\s+receipt_ledger\b') { throw '接続先を別のアプリが使っているため、終了操作を止めました。' }
    $serverChildren += $serverChild
}
# Check every listener before stopping any process.
foreach ($serverChild in $serverChildren) { Stop-Process -Id ([int]$serverChild.ProcessId) }
if (Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue) { Stop-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue }
Remove-Item -LiteralPath $recordPath -Force -ErrorAction SilentlyContinue
Write-Host 'Ryoshuを終了しました。帳簿・原本・Ollamaはそのまま保存されています。'
