# -QaAcceptCrtTermsSha256 is for synthetic QA/CI only (e.g. a disposable hosted runner). It never answers a
# person's prompt: it must name exactly the terms shown, and the consent record says method 'qa_test_flag'.
param([switch]$AcceptModelLicense, [switch]$StartupLockHeld, [string]$QaAcceptCrtTermsSha256 = '')
$ErrorActionPreference = 'Stop'
Write-Host 'Ryoshu — 領収書から支出の下書きと勘定科目の候補を作ります。'
Write-Host '初回準備として、準備ツール・Python・固定版の依存・OCRモデル・llama.cpp実行環境・Microsoft Visual C++パッケージと利用条件・約2.5GBのQwenモデルを外部から取得します。Microsoftパッケージは実行せず、同意後にDLLだけをRyoshu専用の保存先へ配置し、Windows全体へは導入しません。ネット接続と数GB以上の空き容量を用意してください。'
Write-Host 'IndraのOCR重みには、事業規模・資金調達額・競合用途などの利用制限があります。'
Write-Host 'THIRD_PARTY.mdと次のモデル利用条件を確認してください：'
Write-Host 'https://huggingface.co/datalab-to/indra-ocr/blob/bd02167fdf2005a19962cc15188f8496a2c87d3b/LICENSE'
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
. (Join-Path $PSScriptRoot 'scripts\python-runtime.ps1')
. (Join-Path $PSScriptRoot 'scripts\launcher-process.ps1')
Assert-RyoshuUnlinkedPath $taskRoot   # before the first directory, lock, record or download
New-Item -ItemType Directory -Path $taskRoot -Force | Out-Null
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
$runtimePinSha = Get-RyoshuRuntimePinSha256 -SourceDir $PSScriptRoot
Set-RyoshuSetupState -TaskRoot $taskRoot -RequirementsFile (Join-Path $PSScriptRoot 'requirements.lock.txt') -PythonStatus $pythonStatus -State pending -RuntimePinSha256 $runtimePinSha

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
$env:PYTHONUTF8 = '1'
# The Microsoft C++ runtime for the managed Python must be in place before ANY native library is installed,
# probed or imported (sqlcipher3/cryptography probe, onnxruntime, PIL, NumPy, PyPDFium). prepare_llama.py is
# standard library only. The official Microsoft package is used only after the user's explicit choice and is
# placed app-locally (never globally); a decline keeps manual ledger/CSV setup going without OCR/AI.
$crtResult = Join-Path $taskRoot 'runtime\crt-setup-result.json'
$preparer = Join-Path $PSScriptRoot 'scripts\prepare_llama.py'
$pythonBase = [string]$pythonStatus.base_prefix
function Invoke-RyoshuCrtStep([string[]]$Extra) {
    if (Test-Path -LiteralPath $crtResult) { Remove-Item -LiteralPath $crtResult -Force }
    & $pythonExe $preparer --root $taskRoot --crt-only --python-base $pythonBase @Extra
    $code = $LASTEXITCODE
    $report = $null
    try {
        $item = Get-Item -LiteralPath $crtResult -ErrorAction Stop
        if (-not ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -and $item.Length -le 65536) {
            $report = Get-Content -LiteralPath $crtResult -Raw | ConvertFrom-Json
        }
    } catch {}
    $reason = if ($code -eq 0) { '' } elseif ($report -and ([string]$report.reason_code) -match '^[a-z_]{1,40}$') { [string]$report.reason_code } else { 'crt_unavailable' }
    return [PSCustomObject]@{reason=$reason; report=$report}
}
# A user-started setup (初回セットアップ.cmd) may reconsider a recorded decline: the current terms are shown again
# and only an explicit 'y' accepts. Setup run by start.ps1 (-StartupLockHeld) keeps a decline without prompting.
$crt = Invoke-RyoshuCrtStep $(if ($StartupLockHeld) { @() } else { @('--reconsider-crt-terms') })
if ($crt.reason -eq 'crt_terms_pending') {
    # Microsoft's runtime license as received in the official package, shown before any received DLL is placed.
    # [copy-slot setup.crt_terms_intro / setup.crt_terms_identity / setup.crt_terms_prompt] temporary technical text
    $r = $crt.report
    # [copy-slot setup.crt_previously_declined] temporary technical text until Kimi authors it
    if ([string]$r.previous_decision -eq 'declined') { Write-Host '前回はMicrosoft利用条件への同意を見送ったため、今回も条件を確認してから同意するかを選択できます。' }
    Write-Host '以下のMicrosoft利用条件をお読みください。同意した場合に限り、必要なDLLをRyoshu専用の保存先へ配置します。'
    Write-Host ('EULA ID・条件のSHA-256・対象バージョン： ' + [string]$r.eula_id + ' sha256=' + [string]$r.eula_sha256 + ' version=' + [string]$r.version)
    Write-Host ('Microsoft利用条件のタイトルとURL： ' + [string]$r.terms_title + ' ' + [string]$r.terms_url)
    Write-Host ('Microsoft公式パッケージのURLとSHA-256： ' + [string]$r.package_url + ' sha256=' + [string]$r.package_sha256)
    $termsText = [string]$r.terms_text
    if ($termsText -and (Test-Path -LiteralPath $termsText -PathType Leaf)) { Write-Host (Get-Content -LiteralPath $termsText -Raw -Encoding UTF8) }
    if ($QaAcceptCrtTermsSha256) {
        if (([string]$r.eula_sha256) -notmatch '^[0-9a-f]{64}$' -or $QaAcceptCrtTermsSha256 -cne [string]$r.eula_sha256) {
            throw 'Microsoftの配布物または利用条件を準備できませんでした。ネット接続と空き容量を確認し、「初回セットアップ.cmd」で再試行してください。帳簿は変更していません。理由：crt_terms_mismatch'
        }
        # Internal QA machine data (not product copy, never shown): runtime\qa-event-crt-terms.json for the acceptance helper.
        $qaEvent = Join-Path $taskRoot 'runtime\qa-event-crt-terms.json'
        Assert-RyoshuUnlinkedPath $qaEvent
        [System.IO.File]::WriteAllText($qaEvent, ([ordered]@{schema=1; event='crt_terms_qa_test_acceptance'; method='qa_test_flag';
            eula_sha256=[string]$r.eula_sha256; version=[string]$r.version} | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))
        $crt = Invoke-RyoshuCrtStep @('--accept-crt-terms', [string]$r.eula_sha256, '--qa-test-acceptance')
    } elseif (($answer = Read-Host '上記のMicrosoft利用条件に同意し、必要なDLLをRyoshu専用の保存先へ配置しますか？ [y/N]') -match '^[yY]$' -and ([string]$r.eula_sha256) -match '^[0-9a-f]{64}$') {
        $crt = Invoke-RyoshuCrtStep @('--accept-crt-terms', [string]$r.eula_sha256)
    } else {
        $crt = Invoke-RyoshuCrtStep @('--decline-crt-terms')
    }
}
$skipNativePreparation = $false
if ($crt.reason -eq 'crt_declined') {
    $closure = $crt.report.closure
    $missing = @($closure.PSObject.Properties | Where-Object { [string]$_.Value -eq 'missing' })
    # Without the runtime files OCR and local AI cannot load; manual ledger/CSV stay usable and nothing is rolled back.
    if (-not $closure -or $missing.Count -gt 0) { $skipNativePreparation = $true }
    Write-Host 'Microsoft利用条件に同意しなかったため、OCRとAIによる科目候補の準備を行いませんでした。帳簿への手入力とCSV出力は利用できます。あとで同意するか選び直すには「初回セットアップ.cmd」をもう一度実行してください。'
} elseif ($crt.reason) {
    # [copy-slot setup.crt_unavailable] temporary technical message until Kimi authors the text
    throw ('Microsoftの配布物または利用条件を準備できませんでした。ネット接続と空き容量を確認し、「初回セットアップ.cmd」で再試行してください。帳簿は変更していません。理由：' + $crt.reason)
} else {
    # The helper has exited, so the interpreter's own vcruntime140 is no longer mapped: apply the verified stage.
    $applied = Invoke-RyoshuPythonCrtStage -TaskRoot $taskRoot -PythonBase $pythonBase -SourcePin (Join-Path $PSScriptRoot 'receipt_ledger\runtime_pin.json')
    if ($applied -eq 'applied') { Write-Host 'Ryoshu専用PythonのDLLを更新しました。' }
}
Write-Host '1/3 固定版の依存ライブラリを取得しています。'
& $uvExe pip install --python $pythonExe --require-hashes -r (Join-Path $PSScriptRoot 'requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw '依存ライブラリを準備できませんでした。直前のエラー・ネット接続・空き容量を確認し、「初回セットアップ.cmd」で再開してください。' }
$pythonStatus = Get-RyoshuPythonStatus -PythonExe $pythonExe -RequireLibraries
if (-not $pythonStatus.ready) { throw ('依存ライブラリを読み込めませんでした。' + "`n" + (Format-RyoshuPythonIssue $pythonStatus)) }
if ($skipNativePreparation) {
    Set-RyoshuSetupState -TaskRoot $taskRoot -RequirementsFile (Join-Path $PSScriptRoot 'requirements.lock.txt') -PythonStatus $pythonStatus -State complete -RuntimePinSha256 $runtimePinSha -InferenceState failed -InferenceReason 'crt_declined'
    Write-Host ('OCRとAIによる科目候補の準備が完了していません。あとで「初回セットアップ.cmd」を実行してください。帳簿への手入力とCSV出力は利用できます。 [crt_declined]')
    return
}
Write-Host '2/3 文字読み取りモデルを取得しています。'
& $pythonExe (Join-Path $PSScriptRoot 'scripts\prepare_ocr.py')
if ($LASTEXITCODE -ne 0) { throw '文字読み取りモデルを取得できませんでした。直前のエラー・ネット接続・空き容量を確認し、「初回セットアップ.cmd」で再開してください。' }
Write-Host '3/3 勘定科目の候補を作るAIの実行環境とモデルを取得し、サイズとSHA-256を照合しています。中断した場合は「初回セットアップ.cmd」で再開できます。'
# Pinned official llama.cpp runtime + Qwen GGUF (scripts\prepare_llama.py verifies every byte).
# A failure here does not block the base setup: manual ledger/CSV stay usable and the
# technical reason is recorded for a later retry with 初回セットアップ.cmd.
$llamaResult = Join-Path $taskRoot 'runtime\llama-setup-result.json'
if (Test-Path -LiteralPath $llamaResult) { Remove-Item -LiteralPath $llamaResult -Force }
& $pythonExe (Join-Path $PSScriptRoot 'scripts\prepare_llama.py') --root $taskRoot
$inferenceState = 'complete'
$inferenceReason = ''
if ($LASTEXITCODE -ne 0) {
    $inferenceState = 'failed'
    $inferenceReason = 'install_failed'
    try {
        $item = Get-Item -LiteralPath $llamaResult -ErrorAction Stop
        if (-not ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -and $item.Length -le 16384) {
            $reported = [string](Get-Content -LiteralPath $llamaResult -Raw | ConvertFrom-Json).reason_code
            if ($reported -match '^[a-z_]{1,40}$') { $inferenceReason = $reported }
        }
    } catch {}
    # [copy-slot setup.inference_not_ready] temporary technical message until Kimi authors the text
    Write-Host ('OCRとAIによる科目候補の準備が完了していません。あとで「初回セットアップ.cmd」を実行してください。帳簿への手入力とCSV出力は利用できます。 [' + $inferenceReason + ']')
}
Set-RyoshuSetupState -TaskRoot $taskRoot -RequirementsFile (Join-Path $PSScriptRoot 'requirements.lock.txt') -PythonStatus $pythonStatus -State complete -RuntimePinSha256 $runtimePinSha -InferenceState $inferenceState -InferenceReason $inferenceReason
Write-Host '初回準備が完了しました。画面を閉じ、「Ryoshu.cmd」または「起動.cmd」を開いてください。'
Write-Host ('帳簿・原本・OCRモデル・AIモデル・AI実行環境の保存先： ' + $taskRoot)
} finally { if ($setupLock) { $setupLock.Dispose() } }
