param([switch]$AcceptModelLicense, [switch]$StartupLockHeld)
$ErrorActionPreference = 'Stop'
$env:OLLAMA_NO_CLOUD = '1'
Write-Host 'Ryoshu — 領収書から支出の下書きと勘定科目の候補を作ります。'
Write-Host '初回準備として、準備ツール・Python・依存・OCRモデル・約2.5GBのQwenモデルを外部から取得します。ネット接続と数GB以上の空き容量を用意してください。'
Write-Host 'IndraのOCR重みには、事業規模・資金調達額・競合用途などの利用制限があります。'
Write-Host 'THIRD_PARTY.mdと次のモデル利用条件を確認してください：'
Write-Host 'https://huggingface.co/datalab-to/indra-ocr/blob/bd02167fdf2005a19962cc15188f8496a2c87d3b/LICENSE'
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { throw 'Ollamaが見つかりません。https://ollama.com/download からWindows版を導入してから「初回セットアップ.cmd」を実行し直してください。' }
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
New-Item -ItemType Directory -Path $taskRoot -Force | Out-Null
. (Join-Path $PSScriptRoot 'scripts\python-runtime.ps1')
. (Join-Path $PSScriptRoot 'scripts\launcher-process.ps1')
$setupLock = $null
if (-not $StartupLockHeld) { $setupLock = Open-RyoshuStartupLock (Join-Path $taskRoot 'runtime') }
try {
$pythonStatus = Get-RyoshuPythonStatus -PythonExe $pythonExe
try { Assert-RyoshuRuntimeIdle -TaskRoot $taskRoot }
catch {
    $runtimeIssue = if ($pythonStatus.ready) { '' } else { (Format-RyoshuPythonIssue $pythonStatus) + "`n`n" }
    throw ($runtimeIssue + $_.Exception.Message)
}
$licenseRecord = Join-Path $taskRoot 'model-license-acceptance.json'
if (-not $AcceptModelLicense -and -not (Test-Path -LiteralPath $licenseRecord)) {
    $licenseAnswer = Read-Host 'モデルの利用条件を確認し、ダウンロードを開始しますか？ [y/N]'
    if ($licenseAnswer -notmatch '^[yY]$') { throw '準備を取り消しました。利用条件を確認してから、初回セットアップを実行してください。' }
}
if (-not (Test-Path -LiteralPath $licenseRecord)) {
    @{license='Indra modified OpenRAIL-M'; accepted_at=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $licenseRecord -Encoding UTF8
}
Set-RyoshuSetupState -TaskRoot $taskRoot -RequirementsFile (Join-Path $PSScriptRoot 'requirements.lock.txt') -PythonStatus $pythonStatus -State pending

if (-not $pythonStatus.compatible -and (Test-Path -LiteralPath (Join-Path $taskRoot 'venv'))) {
    Write-Host (Format-RyoshuPythonIssue $pythonStatus)
    $savedEnvironment = Move-RyoshuRuntimeAside -Path (Join-Path $taskRoot 'venv') -TaskRoot $taskRoot
    Write-Host ('以前の実行環境を別名で残してから準備し直します： ' + $savedEnvironment)
    Write-Host '帳簿・原本・復元用の情報は移動・削除しません。'
}
. (Join-Path $PSScriptRoot 'scripts\bootstrap-uv.ps1')
$uvExe = Get-RyoshuUv (Join-Path $taskRoot 'tools\uv-0.13.5')
if (-not (Test-Path -LiteralPath $pythonExe)) {
    Write-Host 'Python 3.12.15を、このアプリ専用の保存先に準備します。'
    $pythonHome = Join-Path $taskRoot 'python'
    if ((Test-Path -LiteralPath $pythonHome) -and ((Get-Item -LiteralPath $pythonHome).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { throw 'Pythonの保存先にリンクがあるため、自動で書き込みません。' }
    $managedPython = Join-Path $pythonHome 'cpython-3.12.15-windows-x86_64-none\python.exe'
    $managedStatus = Get-RyoshuPythonStatus -PythonExe $managedPython
    if (-not $managedStatus.compatible) {
        $managedDirectory = Split-Path -Parent $managedPython
        if (Test-Path -LiteralPath $managedDirectory) { $savedPython = Move-RyoshuRuntimeAside -Path $managedDirectory -TaskRoot $taskRoot }
        # Never change another application's uv junctions, PATH aliases or Python
        # registry entries. This interpreter belongs only to Ryoshu.
        & $uvExe python install 3.12.15 --install-dir $pythonHome --no-bin --no-registry
        if ($LASTEXITCODE -ne 0) { throw 'Pythonを取得できませんでした。接続と空き容量を確認してください。以前の実行環境と帳簿は残しています。' }
        $managedStatus = Get-RyoshuPythonStatus -PythonExe $managedPython
        if (-not $managedStatus.compatible) { throw ('取得したPythonを起動できません。' + "`n" + (Format-RyoshuPythonIssue $managedStatus)) }
    }
    & $uvExe venv --python $managedPython (Join-Path $taskRoot 'venv')
    if ($LASTEXITCODE -ne 0) { throw '実行環境を作成できませんでした。保存先と空き容量を確認してください。' }
}
$pythonStatus = Get-RyoshuPythonStatus -PythonExe $pythonExe
if (-not $pythonStatus.ready) { throw (Format-RyoshuPythonIssue $pythonStatus) }
Write-Host '1/3 固定版の依存ライブラリを取得しています。'
& $uvExe pip install --python $pythonExe --require-hashes -r (Join-Path $PSScriptRoot 'requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw '依存ライブラリを準備できませんでした。直前のエラー・ネット接続・空き容量を確認し、「初回セットアップ.cmd」で再開してください。' }
$pythonStatus = Get-RyoshuPythonStatus -PythonExe $pythonExe -RequireLibraries
if (-not $pythonStatus.ready) { throw ('依存ライブラリを読み込めませんでした。' + "`n" + (Format-RyoshuPythonIssue $pythonStatus)) }
$env:PYTHONUTF8 = '1'
Write-Host '2/3 文字読み取りモデルを取得しています。'
& $pythonExe (Join-Path $PSScriptRoot 'scripts\prepare_ocr.py')
if ($LASTEXITCODE -ne 0) { throw '文字読み取りモデルを取得できませんでした。直前のエラー・ネット接続・空き容量を確認し、「初回セットアップ.cmd」で再開してください。' }
$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCommand) { throw 'Ollamaが見つかりません。公式サイトから導入後、初回セットアップを実行し直してください。' }
try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 | Out-Null }
catch {
    Start-Process -FilePath $ollamaCommand.Source -ArgumentList @('serve') -WindowStyle Hidden | Out-Null
    $ollamaReady = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null; $ollamaReady = $true; break } catch {}
    }
    if (-not $ollamaReady) { throw 'Ollamaが起動しませんでした。スタートメニューからOllamaを起動して、初回セットアップを実行し直してください。' }
}
Write-Host '3/3 勘定科目の候補を作るAIモデルを取得しています。'
& $ollamaCommand.Source pull qwen3:4b
if ($LASTEXITCODE -ne 0) { throw 'AIモデルを取得できませんでした。Ollamaの状態・ネット接続・空き容量を確認し、「初回セットアップ.cmd」で再開してください。' }
Set-RyoshuSetupState -TaskRoot $taskRoot -RequirementsFile (Join-Path $PSScriptRoot 'requirements.lock.txt') -PythonStatus $pythonStatus -State complete
Write-Host '初回準備が完了しました。画面を閉じ、「Ryoshu.cmd」または「起動.cmd」を開いてください。'
Write-Host ('帳簿・原本・OCRモデルの保存先： ' + $taskRoot)
} finally { if ($setupLock) { $setupLock.Dispose() } }
