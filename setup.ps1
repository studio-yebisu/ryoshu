param([switch]$AcceptModelLicense)
$ErrorActionPreference = 'Stop'
Write-Host 'Ryoshu — 領収書を読み取り、勘定科目の候補を作ります。'
Write-Host '初回準備では、Pythonの依存ライブラリ、OCRモデル、約2.5GBのAIモデルを取得します。ネット接続と数GB以上の空き容量が必要です。'
Write-Host 'IndraのOCR重みには、事業規模・資金調達額・競合用途などの利用制限があります。'
Write-Host 'THIRD_PARTY.mdと次のモデル利用条件を確認してください：'
Write-Host 'https://huggingface.co/datalab-to/indra-ocr/blob/bd02167fdf2005a19962cc15188f8496a2c87d3b/LICENSE'
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { throw 'Ollamaが見つかりません。https://ollama.com/download からWindows版を導入し、この画面を閉じて「初回セットアップ.cmd」を実行し直してください。' }
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
New-Item -ItemType Directory -Path $taskRoot -Force | Out-Null
$licenseRecord = Join-Path $taskRoot 'model-license-acceptance.json'
if (-not $AcceptModelLicense -and -not (Test-Path -LiteralPath $licenseRecord)) {
    $licenseAnswer = Read-Host 'モデルの利用条件を確認し、ダウンロードを開始しますか？ [y/N]'
    if ($licenseAnswer -notmatch '^[yY]$') { throw '準備を取り消しました。利用条件を確認してから、初回セットアップを実行してください。' }
}
@{license='Indra modified OpenRAIL-M'; accepted_at=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $licenseRecord -Encoding UTF8

if (-not (Test-Path -LiteralPath $pythonExe)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv venv --python 3.12 (Join-Path $taskRoot 'venv')
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv (Join-Path $taskRoot 'venv')
    } else {
        & python -c "import sys;sys.exit(0 if sys.version_info[:2]==(3,12) and sys.maxsize>2**32 else 1)"
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12・64bitが必要です。Python公式サイトから導入するか、uvを用意して実行し直してください。' }
        & python -m venv (Join-Path $taskRoot 'venv')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Python実行環境を作成できませんでした。Python 3.12・64bitまたはuvの導入と、保存先の空き容量を確認してください。' }
}
& $pythonExe -c "import sys;sys.exit(0 if sys.version_info[:2]==(3,12) and sys.maxsize>2**32 else 1)"
if ($LASTEXITCODE -ne 0) { throw '保存済みの実行環境がPython 3.12・64bitではありません。帳簿はそのまま残っています。docs/INSTALL.mdの実行環境の再作成を確認してください。' }
Write-Host '1/3 必要なライブラリを準備しています。'
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv pip install --python $pythonExe -r (Join-Path $PSScriptRoot 'requirements.lock.txt')
} else {
    & $pythonExe -m pip install -r (Join-Path $PSScriptRoot 'requirements.lock.txt')
}
if ($LASTEXITCODE -ne 0) { throw '必要なライブラリを取得できませんでした。直前の詳細とネット接続・空き容量を確認し、初回セットアップを実行し直してください。' }
$env:PYTHONUTF8 = '1'
Write-Host '2/3 文字読み取りモデルを準備しています。'
& $pythonExe (Join-Path $PSScriptRoot 'scripts\prepare_ocr.py')
if ($LASTEXITCODE -ne 0) { throw '文字読み取りモデルを準備できませんでした。直前の詳細とネット接続・空き容量を確認し、初回セットアップを実行し直してください。' }
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
Write-Host '3/3 科目判断のAIモデルを準備しています。'
& $ollamaCommand.Source pull qwen3:4b
if ($LASTEXITCODE -ne 0) { throw 'AIモデルを取得できませんでした。Ollamaの起動、ネット接続、空き容量を確認して、初回セットアップを実行し直してください。' }
Write-Host '準備が完了しました。この画面を閉じ、「Ryoshu.cmd」または「起動.cmd」をダブルクリックしてください。'
Write-Host ('帳簿・原本・OCRモデルの保存先： ' + $taskRoot)
