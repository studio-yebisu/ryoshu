param([switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$appRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
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
    # Local inference readiness from the source pin (stdlib-only receipt_ledger.runtime_pin; never
    # starts a process, never downloads, never hashes the model). python -I drops cwd from sys.path,
    # so the app root is inserted explicitly; works for paths with spaces/Japanese/quotes.
    $inference = [PSCustomObject]@{runtime_ok=$false; model_ok=$false; reason_code='python_not_prepared'; runtime_version=''; model_sha256=''}
    if ($pythonStatus.ready -and -not (Test-Path -LiteralPath (Join-Path $appRoot 'receipt_ledger\runtime_pin.py') -PathType Leaf)) {
        $inference.reason_code = 'readiness_module_missing'
    } elseif ($pythonStatus.ready) {
        try {
            $probe = & $pythonExe -I -B -c 'import sys;sys.path.insert(0,sys.argv[1]);from receipt_ledger.runtime_pin import main;raise SystemExit(main(sys.argv[2:]))' $appRoot --root $taskRoot 2>$null
            $parsed = ($probe | Select-Object -Last 1) | ConvertFrom-Json
            if ($parsed) { $inference = $parsed }
        } catch { $inference = [PSCustomObject]@{runtime_ok=$false; model_ok=$false; reason_code='readiness_check_failed'; runtime_version=''; model_sha256=''} }
    }
    @{app_version=$appVersion; source_complete=($missingSource.Count -eq 0);
        inference_runtime_ready=[bool]$inference.runtime_ok; inference_model_ready=[bool]$inference.model_ok;
        inference_reason=[string]$inference.reason_code; runtime_version=[string]$inference.runtime_version;
        python_prepared=[bool]$pythonStatus.ready; python_exists=[bool]$pythonStatus.exists;
        python_runnable=[bool]$pythonStatus.runnable; python_compatible=[bool]$pythonStatus.compatible;
        python_version=$pythonStatus.version; python_reason=$pythonStatus.reason} | ConvertTo-Json -Compress
    exit 0
}
Add-Type -AssemblyName System.Windows.Forms
# No external model service is required: start.ps1 runs/resumes first setup (which prepares the
# pinned llama.cpp runtime and model) and the app opens even when inference is not yet ready.
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
