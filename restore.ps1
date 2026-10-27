$ErrorActionPreference = 'Stop'
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw '復元にはRyoshu専用の実行環境が必要です。先に「初回セットアップ.cmd」で準備してください。' }
Add-Type -AssemblyName System.Windows.Forms
$picker = New-Object System.Windows.Forms.OpenFileDialog
$picker.Title = '復元するRyoshuバックアップを選択'
$picker.Filter = 'Ryoshuバックアップ (*.ryoshubak;*.zip)|*.ryoshubak;*.zip'
if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Write-Host '復元せずに終了しました。'; exit 0 }
$restoreRoot = Join-Path $taskRoot ('restored\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
Write-Host '内容を検査して新しい保存先へ復元します。現在の帳簿は上書きしません。'
$env:PYTHONUTF8 = '1'
$headerStream = [System.IO.File]::OpenRead($picker.FileName)
try { $isLegacy = ($headerStream.ReadByte() -eq 0x50 -and $headerStream.ReadByte() -eq 0x4b) } finally { $headerStream.Dispose() }
$passwordDialog = New-Object System.Windows.Forms.Form
$passwordDialog.Text = '復元用パスワード'
$passwordDialog.Width = 470; $passwordDialog.Height = 210
$passwordDialog.StartPosition = 'CenterScreen'
$label = New-Object System.Windows.Forms.Label
$label.Text = '暗号化バックアップでは作成時の復元用パスワードを入力してください。旧ZIPでは、これから設定する12〜256文字のパスワードを入力します。'
$label.SetBounds(20,15,420,45)
$box = New-Object System.Windows.Forms.TextBox
$box.UseSystemPasswordChar = $true; $box.MaxLength = 256; $box.SetBounds(20,65,410,25)
$ok = New-Object System.Windows.Forms.Button
$ok.Text = '復元する'; $ok.SetBounds(220,110,100,30); $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = '戻る'; $cancel.SetBounds(330,110,100,30); $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
$confirmation = $null
if ($isLegacy) {
    $passwordDialog.Height = 285
    $confirmLabel = New-Object System.Windows.Forms.Label
    $confirmLabel.Text = '確認のため、もう一度入力してください。'; $confirmLabel.SetBounds(20,100,410,20)
    $confirmation = New-Object System.Windows.Forms.TextBox
    $confirmation.UseSystemPasswordChar = $true; $confirmation.MaxLength = 256; $confirmation.SetBounds(20,125,410,25)
    $passwordDialog.Controls.AddRange(@($confirmLabel,$confirmation))
    $ok.Top = 175; $cancel.Top = 175
}
$passwordDialog.Controls.AddRange(@($label,$box,$ok,$cancel)); $passwordDialog.AcceptButton = $ok; $passwordDialog.CancelButton = $cancel
if ($passwordDialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { $passwordDialog.Dispose(); Write-Host '復元を取り消しました。'; exit 0 }
if ($box.Text.Length -lt 12) { $passwordDialog.Dispose(); throw '復元用パスワードは12文字以上で入力してください。' }
if ($isLegacy -and $box.Text -cne $confirmation.Text) { $box.Clear(); $confirmation.Clear(); $passwordDialog.Dispose(); throw '確認用のパスワードが一致しません。復元は開始していません。' }
$processInfo = New-Object System.Diagnostics.ProcessStartInfo
$processInfo.FileName = $pythonExe
$processInfo.Arguments = '"' + (Join-Path $PSScriptRoot 'scripts\restore_backup.py') + '" "' + $picker.FileName + '" "' + $restoreRoot + '"'
$processInfo.UseShellExecute = $false; $processInfo.CreateNoWindow = $true
$processInfo.RedirectStandardInput = $true; $processInfo.RedirectStandardOutput = $true; $processInfo.RedirectStandardError = $true
$restoreProcess = New-Object System.Diagnostics.Process
$restoreProcess.StartInfo = $processInfo
$restoreProcess.Start() | Out-Null
$passwordBytes = [System.Text.Encoding]::UTF8.GetBytes($box.Text + "`n")
$box.Clear(); $passwordDialog.Dispose()
try { $restoreProcess.StandardInput.BaseStream.Write($passwordBytes,0,$passwordBytes.Length); $restoreProcess.StandardInput.Close() }
finally { [Array]::Clear($passwordBytes,0,$passwordBytes.Length) }
$stdoutTask = $restoreProcess.StandardOutput.ReadToEndAsync()
$stderrTask = $restoreProcess.StandardError.ReadToEndAsync()
$restoreProcess.WaitForExit()
$resultJson = $stdoutTask.Result
if ($restoreProcess.ExitCode -ne 0) { Write-Host $stderrTask.Result; throw '復元を完了できませんでした。元の帳簿は変更していません。' }
$LASTEXITCODE = $restoreProcess.ExitCode
if ($LASTEXITCODE -ne 0) { throw '復元を完了できませんでした。元の帳簿は変更していません。表示された理由を確認してください。' }
$restored = $resultJson | ConvertFrom-Json
Write-Host ('復元しました：' + $restored.receipts + '件 / ' + $restored.restored_data)
. (Join-Path $PSScriptRoot 'scripts\launcher-process.ps1')
$restoredLauncher = New-RyoshuLedgerLauncher -SourceDir $PSScriptRoot -DataDir $restored.restored_data -OutputDir $restoreRoot
Write-Host ('後から開く場合：' + $restoredLauncher)
Write-Host '後で開くときは、先に起動中の帳簿を「停止.cmd」で終了してから「この帳簿を開く.cmd」をダブルクリックしてください。'
$answer = Read-Host '今すぐ復元した帳簿へ切り替えますか？ 現在の帳簿も残ります [y/N]'
if ($answer -match '^[yY]$') {
    & (Join-Path $PSScriptRoot 'stop.ps1')
    & (Join-Path $PSScriptRoot 'start.ps1') -DataDir $restored.restored_data
} else { Write-Host '復元した帳簿を新しい保存先に保存しました。現在の帳簿は変更していません。' }
