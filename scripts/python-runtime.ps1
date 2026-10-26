# Probe the actual interpreter, not the existence of a uv trampoline. These
# helpers never open a ledger, download software, or stop a process.
function Get-RyoshuFileSha256 {
    param([Parameter(Mandatory=$true)][string]$LiteralPath)
    # Windows PowerShell may inherit PowerShell 7's module search path from a
    # launcher. Hashing must work even when Get-FileHash cannot be autoloaded.
    $stream = $null
    $algorithm = $null
    try {
        $stream = [System.IO.File]::Open($LiteralPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
        $algorithm = [System.Security.Cryptography.SHA256]::Create()
        $digest = $algorithm.ComputeHash($stream)
        return [System.BitConverter]::ToString($digest).Replace('-','').ToLowerInvariant()
    } finally {
        if ($algorithm) { $algorithm.Dispose() }
        if ($stream) { $stream.Dispose() }
    }
}

function Get-RyoshuPythonStatus {
    param([string]$PythonExe, [switch]$RequireLibraries)
    $result = [ordered]@{exists=$false;runnable=$false;compatible=$false;ready=$false;version='';reason='missing';detail='';base_prefix=''}
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) { return [PSCustomObject]$result }
    $result.exists = $true
    $probeCode = @'
import sys,json
v=tuple(sys.version_info[:3])
compatible=v[:2]==(3,12) and v>=(3,12,15) and sys.maxsize>2**32
r={'version':'.'.join(map(str,v)),'compatible':compatible,'base_prefix':sys.base_prefix,'libraries':True,'detail':''}
if compatible and sys.argv[1]=='libraries':
    try:
        import cryptography,sqlcipher3
        r['libraries']=cryptography.__version__=='50.0.2' and sqlcipher3.sqlite_version_info>=(3,53,4)
        if not r['libraries']: r['detail']='Required library versions do not match'
    except Exception as e:
        r['libraries']=False
        r['detail']=type(e).__name__+': '+str(e)
print(json.dumps(r,ensure_ascii=True))
'@
    $mode = if ($RequireLibraries) { 'libraries' } else { 'python' }
    $previousPreference = $ErrorActionPreference
    $lines = @()
    $exitCode = $null
    try {
        # Windows PowerShell converts native stderr to ErrorRecord. A broken
        # interpreter must become a diagnostic result, not a different exception.
        $ErrorActionPreference = 'Continue'
        $lines = @(& $PythonExe -I -B -c $probeCode $mode 2>&1)
        $exitCode = $LASTEXITCODE
    } catch {
        $result.reason = 'launch_failed'
        $result.detail = [string]$_.Exception.Message
        return [PSCustomObject]$result
    } finally { $ErrorActionPreference = $previousPreference }
    $text = ($lines | ForEach-Object { [string]$_ }) -join "`n"
    if ($exitCode -ne 0) {
        $result.reason = 'launch_failed'
        $result.detail = $text.Substring(0,[Math]::Min(1200,$text.Length))
        return [PSCustomObject]$result
    }
    try {
        # Keep warnings on stderr from invalidating an otherwise successful
        # probe. Only our complete JSON output line is accepted.
        $jsonLines = @($lines | Where-Object { $_ -is [string] -and $_.Trim().StartsWith('{') })
        if ($jsonLines.Count -ne 1) { throw 'Invalid Python probe output' }
        $info = $jsonLines[0] | ConvertFrom-Json
        $version = [version]([string]$info.version)
        if ($null -eq $info.compatible -or $null -eq $info.libraries -or -not $info.base_prefix) { throw 'Incomplete Python probe output' }
        $result.runnable = $true
        $result.version = [string]$info.version
        $result.base_prefix = [string]$info.base_prefix
        $result.compatible = [bool]$info.compatible -and $version.Major -eq 3 -and $version.Minor -eq 12 -and $version -ge [version]'3.12.15'
        if (-not $result.compatible) { $result.reason = 'unsupported_python'; return [PSCustomObject]$result }
        if ($RequireLibraries -and -not $info.libraries) {
            $result.reason = 'dependencies_missing'
            $result.detail = [string]$info.detail
            return [PSCustomObject]$result
        }
        $result.ready = $true
        $result.reason = 'ready'
        return [PSCustomObject]$result
    } catch {
        $result.reason = 'invalid_probe'
        $result.detail = $text.Substring(0,[Math]::Min(1200,$text.Length))
        return [PSCustomObject]$result
    }
}

function Format-RyoshuPythonIssue {
    param($Status)
    switch ($Status.reason) {
        'missing' { return 'Pythonの実行ファイルが見つかりません。' }
        'unsupported_python' { return ('Python ' + $Status.version + ' が残っています。64bitのPython 3.12.15以降の3.12系へ準備し直します。') }
        'dependencies_missing' { return 'Pythonの必要なライブラリが不足、または対応する版と一致していません。' }
        'launch_failed' { return ('Pythonを起動できません。参照先のPythonが移動・削除された可能性があります。' + "`n" + $Status.detail) }
        default { return ('Pythonの動作を確認できません。' + "`n" + $Status.detail) }
    }
}

function Assert-RyoshuRuntimeIdle {
    param([string]$TaskRoot, [int]$Port=8766)
    $root = [System.IO.Path]::GetFullPath($TaskRoot).TrimEnd('\')
    $prefixes = @(((Join-Path $root 'venv') + '\'), ((Join-Path $root 'python') + '\'))
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop)
    foreach ($process in $processes) {
        foreach ($prefix in $prefixes) {
            if ($process.ExecutablePath -and $process.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) {
                throw 'Ryoshuの実行環境を使用中のPythonがあります。読み取りの終了を待ち、起動元からRyoshuを終了してから準備し直してください。帳簿は変更していません。'
            }
        }
    }
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if (-not $listeners.Count) { return }
    $health = $null
    try { $health = Invoke-RestMethod -Uri ('http://127.0.0.1:' + $Port + '/api/health') -TimeoutSec 3 } catch {}
    if ($health -and $health.app -eq 'receipt-ledger') {
        throw ('Ryoshuが起動中のため、実行環境を変更しませんでした。読み取りの終了を待ち、起動元のRyoshuを終了してから初回セットアップを実行してください。接続先：' + $Port + '番。帳簿は変更していません。')
    }
    throw ('接続先（' + $Port + '番）を別のアプリが使用しています。接続先の競合を解消してから準備し直してください。別のアプリは停止していません。帳簿は変更していません。')
}

function Move-RyoshuRuntimeAside {
    param([string]$Path, [string]$TaskRoot)
    $root = [System.IO.Path]::GetFullPath($TaskRoot).TrimEnd('\')
    $source = [System.IO.Path]::GetFullPath($Path)
    $allowedVenv = Join-Path $root 'venv'
    $allowedPython = Join-Path $root 'python\cpython-3.12.15-windows-x86_64-none'
    if ($source -ine $allowedVenv -and $source -ine $allowedPython) { throw '実行環境以外の保存先は移動しません。' }
    $item = Get-Item -LiteralPath $source -ErrorAction Stop
    if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw '実行環境の保存先にリンクがあるため、自動で移動しません。' }
    $destination = [System.IO.Path]::GetFullPath($source + '-before-update-' + (Get-Date -Format 'yyyyMMddHHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
    if (-not $source.StartsWith($root + '\',[StringComparison]::OrdinalIgnoreCase) -or -not $destination.StartsWith($root + '\',[StringComparison]::OrdinalIgnoreCase)) { throw '実行環境の保存先を確認できません。' }
    Move-Item -LiteralPath $source -Destination $destination -ErrorAction Stop
    return $destination
}

function Set-RyoshuSetupState {
    param([string]$TaskRoot, [string]$RequirementsFile, $PythonStatus,
          [ValidateSet('pending','complete')][string]$State)
    $runtime = Join-Path $TaskRoot 'runtime'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $recordPath = Join-Path $runtime 'setup-state.json'
    if ((Get-Item -LiteralPath $runtime).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw '準備状態の保存先にリンクは使えません。' }
    if ((Test-Path -LiteralPath $recordPath) -and ((Get-Item -LiteralPath $recordPath).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { throw '準備状態の記録にリンクは使えません。' }
    $record = @{schema=1;state=$State;requirements_sha256=(Get-RyoshuFileSha256 -LiteralPath $RequirementsFile);
                python_version=[string]$PythonStatus.version;python_base=[string]$PythonStatus.base_prefix;updated_at=(Get-Date).ToString('o')}
    $temporary = Join-Path $runtime ('setup-state-' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [System.IO.File]::WriteAllText($temporary,($record | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($true)))
        Move-Item -LiteralPath $temporary -Destination $recordPath -Force
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force } }
}

function Test-RyoshuSetupComplete {
    param([string]$TaskRoot, [string]$RequirementsFile, $PythonStatus)
    if (-not $PythonStatus.ready) { return $false }
    $recordPath = Join-Path $TaskRoot 'runtime\setup-state.json'
    if (-not (Test-Path -LiteralPath $recordPath -PathType Leaf)) { return $false }
    try {
        $item = Get-Item -LiteralPath $recordPath
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint -or $item.Length -gt 16384) { return $false }
        $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
        return ($record.schema -eq 1 -and $record.state -ceq 'complete' -and
                $record.requirements_sha256 -ceq (Get-RyoshuFileSha256 -LiteralPath $RequirementsFile) -and
                $record.python_version -ceq [string]$PythonStatus.version -and $record.python_base -ieq [string]$PythonStatus.base_prefix)
    } catch { return $false }
}
