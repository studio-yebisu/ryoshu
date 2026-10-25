param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$appRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
# A fresh Ollama installation may not yet be present in this process's PATH.
$knownOllama = Join-Path $env:LOCALAPPDATA 'Programs\Ollama\ollama.exe'
if ((-not (Get-Command ollama -ErrorAction SilentlyContinue)) -and (Test-Path -LiteralPath $knownOllama)) {
    $env:PATH = (Split-Path -Parent $knownOllama) + [System.IO.Path]::PathSeparator + $env:PATH
}
$ollama = Get-Command ollama -ErrorAction SilentlyContinue
if ($CheckOnly) {
    $versionText = Get-Content -LiteralPath (Join-Path $appRoot 'receipt_ledger\__init__.py') -Raw
    $versionMatch = [regex]::Match($versionText, '__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
    @{app_version=$versionMatch.Groups[1].Value; source_complete=(Test-Path -LiteralPath (Join-Path $appRoot 'web\processing.js')); ollama_available=($null -ne $ollama); python_prepared=(Test-Path -LiteralPath $pythonExe)} | ConvertTo-Json -Compress
    exit 0
}
Add-Type -AssemblyName System.Windows.Forms
if (-not $ollama) {
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "初回の準備には、PC内でAIを動かすOllama Windows版が必要です。`n`n公式ダウンロードページを開きますか？`n導入が終わったら、スタートメニューの「Ryoshu」をもう一度開いてください。`n`nPythonを手動で入れる必要はありません。",
        'Ryoshuの初回準備', [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Information)
    if ($answer -eq [System.Windows.Forms.DialogResult]::Yes) { Start-Process 'https://ollama.com/download/windows' }
    exit 0
}
try {
    & (Join-Path $appRoot 'start.ps1')
} catch {
    $detail = [string]$_.Exception.Message
    Write-Host $detail -ForegroundColor Red
    [System.Windows.Forms.MessageBox]::Show(
        "Ryoshuを開けませんでした。`n`n$detail`n`n導入ガイドは、スタートメニューの「Ryoshuの導入ガイド」から開けます。帳簿は削除していません。",
        'Ryoshu', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
    exit 1
}
