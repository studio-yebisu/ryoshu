param([switch]$NoBrowser, [string]$DataDir)
$ErrorActionPreference = 'Stop'
$env:OLLAMA_NO_CLOUD = '1'
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
$appUrl = 'http://127.0.0.1:8766'
$runtimeDir = Join-Path $taskRoot 'runtime'
. (Join-Path $PSScriptRoot 'scripts\launcher-process.ps1')
. (Join-Path $PSScriptRoot 'scripts\python-runtime.ps1')
$startupLock = Open-RyoshuStartupLock $runtimeDir
try {
$locationRecord = Join-Path $taskRoot 'runtime\data-location.json'
$selectedData = if ($DataDir) { [System.IO.Path]::GetFullPath($DataDir) } elseif (Test-Path -LiteralPath $locationRecord) {
    (Get-Content -LiteralPath $locationRecord -Raw | ConvertFrom-Json).data_dir
} else { Join-Path $taskRoot 'data' }
if (($DataDir -or (Test-Path -LiteralPath $locationRecord)) -and -not (Test-Path -LiteralPath (Join-Path $selectedData 'ledger.sqlite3')) -and -not (Test-Path -LiteralPath (Join-Path $selectedData 'ledger.enc.sqlite3'))) {
    throw '選択した帳簿が見つかりません。保存先の場所を確認するか、バックアップから復元してください。新しい空の帳簿へ自動で切り替えず、既存の帳簿も変更していません。'
}
$pythonStatus = Get-RyoshuPythonStatus -PythonExe $pythonExe -RequireLibraries
if (-not $pythonStatus.ready) {
    Write-Host (Format-RyoshuPythonIssue $pythonStatus)
    & (Join-Path $PSScriptRoot 'setup.ps1') -StartupLockHeld
    $pythonStatus = Get-RyoshuPythonStatus -PythonExe $pythonExe -RequireLibraries
    if (-not $pythonStatus.ready) { throw ('実行環境の準備が完了していません。' + "`n" + (Format-RyoshuPythonIssue $pythonStatus)) }
}
$sourceVersion = & $pythonExe -c "import sys;sys.path.insert(0,sys.argv[1]);from receipt_ledger import __version__;print(__version__)" $PSScriptRoot

try { $runningApp = Invoke-RestMethod -Uri ($appUrl + '/api/health') -TimeoutSec 4 } catch { $runningApp = $null }
if ($runningApp -and $runningApp.app -eq 'receipt-ledger') {
    $previousRecord = $null
    $serverRecord = Join-Path $runtimeDir 'server.json'
    if (Test-Path -LiteralPath $serverRecord) { try { $previousRecord = Get-Content -LiteralPath $serverRecord -Raw | ConvertFrom-Json } catch {} }
    $existingServers = @(Get-RyoshuListener -PythonExe $pythonExe -Record $previousRecord)
    if ($existingServers.Count -ne 1) { throw '起動済みアプリの接続元を特定できません。少し待ってから起動し直してください。' }
    if (-not $runningApp.instance) { throw '旧版のRyoshuが起動中です。起動元の「停止.cmd」で終了してから開いてください。' }
    $sessionJson = & $pythonExe (Join-Path $PSScriptRoot 'scripts\open_session.py') --data-dir $selectedData
    if ($LASTEXITCODE -ne 0) { throw '起動中の帳簿の保護情報を読み取れません。保存先と起動元を確認してください。' }
    $session = $sessionJson | ConvertFrom-Json
    if ($session.instance -ne $runningApp.instance -or [int]$session.pid -ne [int]$existingServers[0].ProcessId) { throw '起動情報と接続中のプロセスが一致しません。「停止.cmd」で終了してから起動し直してください。' }
    $runningApp = $session
    Save-RyoshuServerRecord -RuntimeDir $runtimeDir -Server $existingServers[0] -DataDir $session.data_dir -SourceDir ([string]$session.source_dir)
    if ($runningApp.app_version -ne $sourceVersion) { throw '別の版のRyoshuが起動しています。「停止.cmd」で終了してから、もう一度起動してください。' }
    if ([System.IO.Path]::GetFullPath($runningApp.data_dir) -ine [System.IO.Path]::GetFullPath($selectedData)) { throw '別の保存先の帳簿が開いています。「停止.cmd」で終了してから起動してください。現在の帳簿は変更していません。' }
    if ($runningApp.source_dir -and ([System.IO.Path]::GetFullPath($runningApp.source_dir) -ine [System.IO.Path]::GetFullPath($PSScriptRoot))) { throw '別のフォルダーのRyoshuが起動しています。「停止.cmd」で終了してから起動してください。' }
    if (-not $NoBrowser) { Start-Process $session.url }
    Write-Host ('起動済みです： ' + $appUrl)
    exit 0
}
if (Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue) { throw 'Ryoshuの接続先（8766番）が別のアプリで使われています。使っているアプリを確認してから起動してください。' }
if (-not (Test-RyoshuSetupComplete -TaskRoot $taskRoot -RequirementsFile (Join-Path $PSScriptRoot 'requirements.lock.txt') -PythonStatus $pythonStatus)) {
    Write-Host '初回セットアップの完了を確認できません。残っている準備を再開します。'
    & (Join-Path $PSScriptRoot 'setup.ps1') -StartupLockHeld
    $pythonStatus = Get-RyoshuPythonStatus -PythonExe $pythonExe -RequireLibraries
    if (-not (Test-RyoshuSetupComplete -TaskRoot $taskRoot -RequirementsFile (Join-Path $PSScriptRoot 'requirements.lock.txt') -PythonStatus $pythonStatus)) { throw '初回セットアップはまだ完了していません。直前のエラーを確認して実行し直してください。帳簿は残っています。' }
}
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
        if ($runningApp.app -eq 'receipt-ledger' -and $runningApp.app_version -eq $sourceVersion -and $runningApp.instance) {
            $listeners = @(Get-RyoshuListener -PythonExe $pythonExe)
            if ($listeners.Count -eq 1 -and ($listeners[0].ProcessId -eq $serverProcess.Id -or $listeners[0].ParentProcessId -eq $serverProcess.Id)) { $appReady = $true; break }
        }
    } catch {}
    if ($serverProcess.HasExited) { break }
}
if (-not $appReady) { throw ('起動できませんでした。次のログで詳細を確認してください： ' + (Join-Path $taskRoot 'runtime\stderr.log')) }
$sessionJson = & $pythonExe (Join-Path $PSScriptRoot 'scripts\open_session.py') --data-dir $selectedData
if ($LASTEXITCODE -ne 0) { throw '起動情報を確認できません。Ryoshuを起動し直してください。' }
$session = $sessionJson | ConvertFrom-Json
if ($session.instance -ne $runningApp.instance -or [int]$session.pid -ne [int]$listeners[0].ProcessId -or [System.IO.Path]::GetFullPath($session.data_dir) -ine [System.IO.Path]::GetFullPath($selectedData) -or [System.IO.Path]::GetFullPath($session.source_dir) -ine [System.IO.Path]::GetFullPath($PSScriptRoot)) { throw '起動情報と接続先が一致しません。帳簿は変更していません。' }
Save-RyoshuServerRecord -RuntimeDir $runtimeDir -Server $listeners[0] -DataDir $selectedData -SourceDir $PSScriptRoot
# The first-run protection screen has no ledger yet. Do not record an empty
# directory as an established ledger: reopening that screen must remain possible.
if ((Test-Path -LiteralPath (Join-Path $selectedData 'ledger.sqlite3')) -or (Test-Path -LiteralPath (Join-Path $selectedData 'ledger.enc.sqlite3'))) {
    @{data_dir=$selectedData} | ConvertTo-Json | Set-Content -LiteralPath $locationRecord -Encoding UTF8
}
if (-not $NoBrowser) { Start-Process $session.url }
Write-Host ('起動しました： ' + $appUrl)
Write-Host ('開いている帳簿： ' + $selectedData)
} finally { $startupLock.Dispose() }
