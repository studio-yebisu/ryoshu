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
    $versionFile = Join-Path $appRoot 'receipt_ledger\__init__.py'
    $appVersion = $null
    if (Test-Path -LiteralPath $versionFile -PathType Leaf) {
        $versionText = Get-Content -LiteralPath $versionFile -Raw
        $versionMatch = [regex]::Match($versionText, '__version__\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"')
        if ($versionMatch.Success) { $appVersion = $versionMatch.Groups[1].Value }
    }
    $requiredSource = @('receipt_ledger\__init__.py', 'web\processing.js',
        'scripts\python-runtime.ps1', 'scripts\bootstrap-uv.ps1', 'scripts\launcher-process.ps1')
    $missingSource = @($requiredSource | Where-Object { -not (Test-Path -LiteralPath (Join-Path $appRoot $_) -PathType Leaf) })
    $runtimeHelper = Join-Path $appRoot 'scripts\python-runtime.ps1'
    if (Test-Path -LiteralPath $runtimeHelper -PathType Leaf) {
        . $runtimeHelper
        $pythonStatus = Get-RyoshuPythonStatus -PythonExe $pythonExe -RequireLibraries
    } else {
        $pythonStatus = [PSCustomObject]@{exists=(Test-Path -LiteralPath $pythonExe -PathType Leaf); runnable=$false;
            compatible=$false; ready=$false; version=''; reason='runtime_helper_missing'}
    }
    @{app_version=$appVersion; source_complete=($missingSource.Count -eq 0); ollama_available=($null -ne $ollama);
        python_prepared=[bool]$pythonStatus.ready; python_exists=[bool]$pythonStatus.exists;
        python_runnable=[bool]$pythonStatus.runnable; python_compatible=[bool]$pythonStatus.compatible;
        python_version=$pythonStatus.version; python_reason=$pythonStatus.reason} | ConvertTo-Json -Compress
    exit 0
}
Add-Type -AssemblyName System.Windows.Forms
if (-not $ollama) {
    $answer = [System.Windows.Forms.MessageBox]::Show(
        "領収書をローカルPC上で処理するには、Ollama Windows版が必要です。`n`n公式ダウンロードページを開きますか？`n導入後はスタートメニューの「Ryoshu」を開き直してください。初回準備ではネット接続も必要です。`n`nPythonを手動で導入する必要はありません。",
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
        "Ryoshuを起動できませんでした。`n`n$detail`n`n表示された原因を確認してください。手順はスタートメニューの「Ryoshuの導入ガイド」にあります。帳簿と原本は削除していません。",
        'Ryoshu', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
    exit 1
}
