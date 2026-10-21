$ErrorActionPreference = 'Stop'
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw '先に「初回セットアップ.cmd」を実行してください。復元にはこのアプリの実行環境が必要です。' }
Add-Type -AssemblyName System.Windows.Forms
$picker = New-Object System.Windows.Forms.OpenFileDialog
$picker.Title = 'RyoshuのバックアップZIPを選んでください'
$picker.Filter = 'バックアップZIP (*.zip)|*.zip'
if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Write-Host '復元を取り消しました。'; exit 0 }
$restoreRoot = Join-Path $taskRoot ('restored\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
Write-Host 'バックアップを確認して、新しい保存先に復元します。元の帳簿は上書きしません。'
$env:PYTHONUTF8 = '1'
$resultJson = & $pythonExe (Join-Path $PSScriptRoot 'scripts\restore_backup.py') $picker.FileName $restoreRoot
if ($LASTEXITCODE -ne 0) { throw '復元を完了できませんでした。元の帳簿は変更していません。表示された理由を確認してください。' }
$restored = $resultJson | ConvertFrom-Json
Write-Host ('復元しました：' + $restored.receipts + '件 / ' + $restored.restored_data)
$answer = Read-Host '復元した帳簿に切り替えて起動しますか？ 元の帳簿は保存されています [y/N]'
if ($answer -match '^[yY]$') {
    & (Join-Path $PSScriptRoot 'stop.ps1')
    & (Join-Path $PSScriptRoot 'start.ps1') -DataDir $restored.restored_data
} else { Write-Host '復元したデータを保存しました。現在使っている帳簿は変更していません。' }
