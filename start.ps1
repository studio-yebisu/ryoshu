param([switch]$NoBrowser, [string]$DataDir)
$ErrorActionPreference = 'Stop'
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
$appUrl = 'http://127.0.0.1:8766'
$runtimeDir = Join-Path $taskRoot 'runtime'
. (Join-Path $PSScriptRoot 'scripts\launcher-process.ps1')
$startupLock = Open-RyoshuStartupLock $runtimeDir
try {
if (-not (Test-Path -LiteralPath $pythonExe)) { & (Join-Path $PSScriptRoot 'setup.ps1') }
$locationRecord = Join-Path $taskRoot 'runtime\data-location.json'
$selectedData = if ($DataDir) { [System.IO.Path]::GetFullPath($DataDir) } elseif (Test-Path -LiteralPath $locationRecord) {
    (Get-Content -LiteralPath $locationRecord -Raw | ConvertFrom-Json).data_dir
} else { Join-Path $taskRoot 'data' }
if (($DataDir -or (Test-Path -LiteralPath $locationRecord)) -and -not (Test-Path -LiteralPath (Join-Path $selectedData 'ledger.sqlite3'))) {
    throw '選択した帳簿が見つかりません。保存先を移動していないか確認してください。新しい空の帳簿へ自動で切り替えることはありません。'
}
& $pythonExe -c "import sys;sys.exit(0 if sys.version_info[:2]==(3,12) and sys.maxsize>2**32 else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12・64bitの実行環境が必要です。docs/INSTALL.mdの実行環境の再作成を確認してください。帳簿は変更していません。' }
$sourceVersion = & $pythonExe -c "import sys;sys.path.insert(0,sys.argv[1]);from receipt_ledger import __version__;print(__version__)" $PSScriptRoot

try { $runningApp = Invoke-RestMethod -Uri ($appUrl + '/api/health') -TimeoutSec 4 } catch { $runningApp = $null }
if ($runningApp -and $runningApp.app -eq 'receipt-ledger') {
    $previousRecord = $null
    $serverRecord = Join-Path $runtimeDir 'server.json'
    if (Test-Path -LiteralPath $serverRecord) { try { $previousRecord = Get-Content -LiteralPath $serverRecord -Raw | ConvertFrom-Json } catch {} }
    $existingServers = @(Get-RyoshuListener -PythonExe $pythonExe -Record $previousRecord)
    if ($existingServers.Count -ne 1) { throw '起動済みアプリの接続元を確認できません。しばらく待ってから、もう一度操作してください。' }
    Save-RyoshuServerRecord -RuntimeDir $runtimeDir -Server $existingServers[0] -DataDir $runningApp.data_dir -SourceDir ([string]$runningApp.source_dir)
    if ($runningApp.app_version -ne $sourceVersion) { throw '別の版のRyoshuが起動しています。「停止.cmd」で終了してから、もう一度起動してください。' }
    if ([System.IO.Path]::GetFullPath($runningApp.data_dir) -ine [System.IO.Path]::GetFullPath($selectedData)) { throw '別の保存先の帳簿が開いています。「停止.cmd」で終了してから起動してください。現在の帳簿は変更していません。' }
    if ($runningApp.source_dir -and ([System.IO.Path]::GetFullPath($runningApp.source_dir) -ine [System.IO.Path]::GetFullPath($PSScriptRoot))) { throw '別のフォルダーのRyoshuが起動しています。「停止.cmd」で終了してから起動してください。' }
    if (-not $NoBrowser) { Start-Process $appUrl }
    Write-Host ('起動済みです： ' + $appUrl)
    exit 0
}
if (Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue) { throw 'Ryoshuの接続先（8766番）が別のアプリで使われています。使っているアプリを確認してから起動してください。' }
try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 | Out-Null }
catch {
    $ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
    if ($ollamaCommand) { Start-Process -FilePath $ollamaCommand.Source -ArgumentList @('serve') -WindowStyle Hidden | Out-Null }
}
$env:PYTHONUTF8 = '1'
$env:RECEIPT_LEDGER_PORT = '8766'
$env:RECEIPT_LEDGER_DATA = $selectedData
New-Item -ItemType Directory -Path (Join-Path $taskRoot 'runtime') -Force | Out-Null
$serverProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','receipt_ledger') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRoot 'runtime\stdout.log') -RedirectStandardError (Join-Path $taskRoot 'runtime\stderr.log')
$appReady = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $runningApp = Invoke-RestMethod -Uri ($appUrl + '/api/health') -TimeoutSec 4
        if ($runningApp.app -eq 'receipt-ledger' -and $runningApp.app_version -eq $sourceVersion -and ([System.IO.Path]::GetFullPath($runningApp.data_dir) -ieq [System.IO.Path]::GetFullPath($selectedData))) {
            $listeners = @(Get-RyoshuListener -PythonExe $pythonExe)
            if ($listeners.Count -eq 1 -and ($listeners[0].ProcessId -eq $serverProcess.Id -or $listeners[0].ParentProcessId -eq $serverProcess.Id)) { $appReady = $true; break }
        }
    } catch {}
    if ($serverProcess.HasExited) { break }
}
if (-not $appReady) { throw ('起動できませんでした。次のログで詳細を確認してください： ' + (Join-Path $taskRoot 'runtime\stderr.log')) }
Save-RyoshuServerRecord -RuntimeDir $runtimeDir -Server $listeners[0] -DataDir $selectedData -SourceDir $PSScriptRoot
@{data_dir=$selectedData} | ConvertTo-Json | Set-Content -LiteralPath $locationRecord -Encoding UTF8
if (-not $NoBrowser) { Start-Process $appUrl }
Write-Host ('起動しました： ' + $appUrl)
Write-Host ('開いている帳簿： ' + $selectedData)
} finally { $startupLock.Dispose() }
