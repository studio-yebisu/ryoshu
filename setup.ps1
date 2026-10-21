$ErrorActionPreference = 'Stop'
Write-Host 'Ryoshu: local receipt OCR and account suggestions'
Write-Host 'Setup downloads Python dependencies, Indra weights and Qwen3:4b.'
Write-Host 'Indra weights: modified OpenRAIL-M; commercial/revenue/funding/competition restrictions apply.'
Write-Host 'Review THIRD_PARTY.md and the model license before running setup:'
Write-Host 'https://huggingface.co/datalab-to/indra-ocr/blob/bd02167fdf2005a19962cc15188f8496a2c87d3b/LICENSE'
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { throw 'Install Ollama from https://ollama.com/download before running setup.ps1.' }
$taskRoot = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger'
$pythonExe = Join-Path $taskRoot 'venv\Scripts\python.exe'
New-Item -ItemType Directory -Path $taskRoot -Force | Out-Null
if (-not (Test-Path -LiteralPath $pythonExe)) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv venv --python 3.12 (Join-Path $taskRoot 'venv')
    } else {
        & python -m venv (Join-Path $taskRoot 'venv')
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the app runtime.' }
}
if (Get-Command uv -ErrorAction SilentlyContinue) {
    & uv pip install --python $pythonExe -r (Join-Path $PSScriptRoot 'requirements.lock.txt')
} else {
    & $pythonExe -m pip install -r (Join-Path $PSScriptRoot 'requirements.lock.txt')
}
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
$env:PYTHONUTF8 = '1'
& $pythonExe (Join-Path $PSScriptRoot 'scripts\prepare_ocr.py')
if ($LASTEXITCODE -ne 0) { throw 'OCR model setup failed.' }
$ollamaCommand = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaCommand) { throw 'Install Ollama from https://ollama.com/download, then run setup.ps1 again.' }
try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 3 | Out-Null }
catch {
    Start-Process -FilePath $ollamaCommand.Source -ArgumentList @('serve') -WindowStyle Hidden | Out-Null
    $ollamaReady = $false
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 500
        try { Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null; $ollamaReady = $true; break } catch {}
    }
    if (-not $ollamaReady) { throw 'Ollama did not start on 127.0.0.1:11434.' }
}
& $ollamaCommand.Source pull qwen3:4b
if ($LASTEXITCODE -ne 0) { throw 'Local model download failed.' }
Write-Host 'Setup complete. Run start.ps1 or Ryoshu.cmd.'
Write-Host ('Private data and OCR cache: ' + $taskRoot)
