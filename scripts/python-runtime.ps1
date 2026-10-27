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
        # same pre-native activation as the app: map the verified app-owned CRT before any extension loads
        sys.path.insert(0,sys.argv[2])
        from receipt_ledger import crt_bootstrap
        r['crt']=crt_bootstrap.activate(sys.argv[3])['state']
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
        # <root>\venv\Scripts\python.exe -> <root>; the app source is this script's parent directory
        $probeRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PythonExe))
        $lines = @(& $PythonExe -I -B -c $probeCode $mode (Split-Path -Parent $PSScriptRoot) $probeRoot 2>&1)
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
        'missing' { return 'Ryoshu専用のPythonが見つかりません。「初回セットアップ.cmd」で準備してください。' }
        'unsupported_python' { return ('Python ' + $Status.version + ' は対象外です。64bitのPython 3.12.15以降の3.12系を「初回セットアップ.cmd」で準備してください。') }
        'dependencies_missing' { return '必要なPythonライブラリが不足しているか、固定版と一致しません。「初回セットアップ.cmd」で再取得してください。' }
        'launch_failed' { return ('Ryoshu専用Pythonを起動できません。移動・削除されていないか確認し、「初回セットアップ.cmd」で準備し直してください。' + "`n" + $Status.detail) }
        default { return ('Pythonの状態を確認できませんでした。「初回セットアップ.cmd」で準備し直してください。' + "`n" + $Status.detail) }
    }
}

function Assert-RyoshuRuntimeIdle {
    param([string]$TaskRoot, [int]$Port=8766)
    $root = [System.IO.Path]::GetFullPath($TaskRoot).TrimEnd('\')
    $prefixes = @(((Join-Path $root 'venv') + '\'), ((Join-Path $root 'python') + '\'), ((Join-Path $root 'llama') + '\'))
    # Path-based only: a llama-server.exe elsewhere (another app, a user's own copy) is never Ryoshu's.
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='llama-server.exe'" -ErrorAction Stop)
    foreach ($process in $processes) {
        foreach ($prefix in $prefixes) {
            if ($process.ExecutablePath -and $process.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) {
                throw 'RyoshuのAI処理が実行中のため、実行環境を変更できません。読み取りの終了を待ち、起動元の「停止.cmd」でRyoshuを終了してから「初回セットアップ.cmd」を実行してください。他のアプリは停止せず、帳簿は変更していません。'
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

function Assert-RyoshuUnlinkedPath {
    # Refuse a path when it or ANY existing ancestor is a junction/symlink/reparse point, BEFORE anything is
    # created, locked or downloaded under it. GetFullPath is purely lexical (it never resolves reparse points),
    # so the original link stays visible; GetAttributes reports the link's own attributes without following it.
    param([string]$Path)
    # relative paths are made absolute against the PowerShell location, lexically (no link resolution)
    $current = [System.IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Path)).TrimEnd('\')
    while ($current) {
        $attributes = $null
        try { $attributes = [System.IO.File]::GetAttributes($current) }
        catch [System.IO.FileNotFoundException] {} catch [System.IO.DirectoryNotFoundException] {}
        if ($null -ne $attributes -and ($attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
            # [copy-slot path.link_refused] temporary technical message until Kimi authors the text
            throw '保存先またはその上位フォルダーにリンクが使われているため、自動で書き込みません。リンクではない通常のフォルダーを保存先に選び直してください。'
        }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -eq $current) { break }
        $current = $parent.TrimEnd('\')
    }
}

function Invoke-RyoshuPythonCrtStage {
    # Apply <root>\crt\python-stage\ (written by prepare_llama.py --crt-only) to the app-managed interpreter AFTER
    # every Python process has exited: a mapped vcruntime140.dll cannot be overwritten. Only an interpreter strictly
    # inside <root>\python\ is touched; replaced bytes are kept in <root>\crt\python-backup-<time>\ with their
    # hashes, and any failure restores them. Returns 'none' (no plan), 'applied', or throws.
    # AUTHORITY: -SourcePin is the caller's own receipt_ledger\runtime_pin.json (never a path taken from stage data).
    # Every value (pin SHA-256, version, the exact four names, sizes, SHA-256) is checked against it BEFORE any write;
    # plan.json is only cross-checked, never trusted.
    param([string]$TaskRoot, [string]$PythonBase, [string]$SourcePin)
    $invalid = '準備したDLLまたは配置情報の整合性を確認できないため、Pythonを変更していません。「初回セットアップ.cmd」で再試行してください。'   # [copy-slot setup.crt_python_stage_invalid]
    $root = [System.IO.Path]::GetFullPath($TaskRoot).TrimEnd('\')
    $stage = Join-Path $root 'crt\python-stage'
    if (-not (Test-Path -LiteralPath $stage)) { return 'none' }
    Assert-RyoshuUnlinkedPath $stage
    $names = @('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll','msvcp140_1.dll')
    # 1. the authoritative source pin
    if (-not $SourcePin) { throw $invalid }
    Assert-RyoshuUnlinkedPath $SourcePin
    $pinItem = Get-Item -LiteralPath $SourcePin -ErrorAction Stop
    if ($pinItem.PSIsContainer -or $pinItem.Length -gt 1048576) { throw $invalid }
    $pinSha = Get-RyoshuFileSha256 -LiteralPath $SourcePin
    try { $pinCrt = ([System.IO.File]::ReadAllText($SourcePin) | ConvertFrom-Json).crt } catch { throw $invalid }
    if (-not $pinCrt -or [string]$pinCrt.version -notmatch '^\d+(\.\d+){3}$') { throw $invalid }
    $want = @{}
    foreach ($f in @($pinCrt.files)) {
        $n = [string]$f.name
        if ($names -notcontains $n -or $want.ContainsKey($n) -or [string]$f.sha256 -notmatch '^[0-9a-f]{64}$' -or [long]$f.bytes -le 0) { throw $invalid }
        $want[$n] = @{sha256=[string]$f.sha256; bytes=[long]$f.bytes}
    }
    if ($want.Count -ne 4 -or (@($pinCrt.python_files) | Sort-Object) -join ',' -ne (($names | Sort-Object) -join ',')) { throw $invalid }
    # 2. stage directory: exactly plan.json + the four DLLs, no links or subfolders
    $entries = @(Get-ChildItem -LiteralPath $stage -Force)
    if ($entries.Count -ne 5 -or @($entries | Where-Object { $_.PSIsContainer -or ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) }).Count -or
        ((@($entries.Name) | Sort-Object) -join ',') -ne ((@($names + 'plan.json') | Sort-Object) -join ',')) { throw $invalid }
    # 3. plan.json: exact shape (raw key counts catch duplicate keys that ConvertFrom-Json would merge)
    $planPath = Join-Path $stage 'plan.json'
    if ((Get-Item -LiteralPath $planPath).Length -gt 16384) { throw $invalid }
    $raw = [System.IO.File]::ReadAllText($planPath)
    $keys = @([regex]::Matches($raw, '"((?:[^"\\]|\\.)*)"\s*:') | ForEach-Object { $_.Groups[1].Value })
    $expected = @{schema=1; source_pin_sha256=1; version=1; python_base=1; created_at=1; files=1; bytes=4; sha256=4; current_sha256=4}
    foreach ($n in $names) { $expected[$n] = 1 }
    $counts = @{}
    foreach ($k in $keys) { $counts[$k] = 1 + [int]$counts[$k] }
    if ($counts.Count -ne $expected.Count) { throw $invalid }
    foreach ($k in $expected.Keys) { if ($counts[$k] -ne $expected[$k]) { throw $invalid } }
    try { $plan = $raw | ConvertFrom-Json } catch { throw $invalid }
    if ($plan.schema -ne 1 -or [string]$plan.source_pin_sha256 -cne $pinSha -or [string]$plan.version -cne [string]$pinCrt.version) { throw $invalid }
    if (((@($plan.files.PSObject.Properties.Name) | Sort-Object) -join ',') -ne (($names | Sort-Object) -join ',')) { throw $invalid }
    foreach ($n in $names) {
        $p = $plan.files.$n
        $staged = Get-Item -LiteralPath (Join-Path $stage $n)
        if ([string]$p.sha256 -cne $want[$n].sha256 -or [long]$p.bytes -ne $want[$n].bytes -or $staged.Length -ne $want[$n].bytes -or
            (Get-RyoshuFileSha256 -LiteralPath $staged.FullName) -cne $want[$n].sha256) { throw $invalid }
    }
    # 4. target: only the app-managed interpreter the caller is preparing
    $base = [System.IO.Path]::GetFullPath([string]$plan.python_base).TrimEnd('\')
    $pythonHome = (Join-Path $root 'python') + '\'
    # [copy-slot setup.crt_python_not_owned] temporary technical message until Kimi authors the text
    if ($base -ine [System.IO.Path]::GetFullPath($PythonBase).TrimEnd('\') -or
        -not $base.StartsWith($pythonHome,[StringComparison]::OrdinalIgnoreCase)) { throw 'Ryoshu専用のPython保存先を確認できないため、DLLを変更していません。「初回セットアップ.cmd」を実行してPythonを準備し直してください。' }
    Assert-RyoshuUnlinkedPath $base
    $backup = Join-Path $root ('crt\python-backup-' + (Get-Date -Format 'yyyyMMddHHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
    New-Item -ItemType Directory -Path $backup | Out-Null
    $kept = [ordered]@{}
    foreach ($n in $names) {
        $dest = Join-Path $base $n
        if (Test-Path -LiteralPath $dest) {
            Copy-Item -LiteralPath $dest -Destination (Join-Path $backup $n)
            $kept[$n] = Get-RyoshuFileSha256 -LiteralPath (Join-Path $backup $n)
            if ($kept[$n] -ne (Get-RyoshuFileSha256 -LiteralPath $dest)) { throw $invalid }
        } else { $kept[$n] = $null }
    }
    $manifest = [ordered]@{schema=1; python_base=$base; version=[string]$plan.version; replaced=$kept; created_at=(Get-Date).ToString('o')}
    [System.IO.File]::WriteAllText((Join-Path $backup 'backup.json'), ($manifest | ConvertTo-Json), (New-Object System.Text.UTF8Encoding($false)))
    $done = @()
    try {
        foreach ($n in $names) {
            $dest = Join-Path $base $n
            $tmp = Join-Path $base ('.' + $n + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
            Copy-Item -LiteralPath (Join-Path $stage $n) -Destination $tmp
            if ((Get-RyoshuFileSha256 -LiteralPath $tmp) -cne $want[$n].sha256) { throw $invalid }   # re-check the copy against the PIN
            Move-Item -LiteralPath $tmp -Destination $dest -Force   # fails (nothing replaced) if the DLL is in use
            $done += $n
            if ((Get-RyoshuFileSha256 -LiteralPath $dest) -cne $want[$n].sha256) { throw $invalid }
        }
    } catch {
        foreach ($n in $done) {
            if ($kept[$n]) { Copy-Item -LiteralPath (Join-Path $backup $n) -Destination (Join-Path $base $n) -Force }
            else { Remove-Item -LiteralPath (Join-Path $base $n) -Force }
        }
        Get-ChildItem -LiteralPath $base -Filter '.*.tmp' -Force | Where-Object { $_.Name -match '^\.(msvcp|vcruntime)' } | Remove-Item -Force
        # [copy-slot setup.crt_python_apply_failed] temporary technical message until Kimi authors the text
        throw ('Ryoshu専用PythonのDLLを更新できませんでした。変更したファイルはバックアップから元に戻しています。「停止.cmd」でRyoshuを終了し、「初回セットアップ.cmd」で再試行してください。準備済みのDLLは再試行用に残しています。詳細：' + $_.Exception.Message)
    }
    Move-Item -LiteralPath $stage -Destination ($backup + '\applied-stage')
    return 'applied'
}

function Get-RyoshuRuntimePinSha256 {
    # The source pin (receipt_ledger\runtime_pin.json) is the only authority; '' when absent.
    param([string]$SourceDir)
    $pin = Join-Path $SourceDir 'receipt_ledger\runtime_pin.json'
    if (-not (Test-Path -LiteralPath $pin -PathType Leaf)) { return '' }
    return (Get-RyoshuFileSha256 -LiteralPath $pin)
}

function Get-RyoshuSetupRecordPinSha256 {
    # Pin hash recorded by the last setup; '' for a missing/unreadable/schema-1 record.
    param([string]$TaskRoot)
    $recordPath = Join-Path $TaskRoot 'runtime\setup-state.json'
    try {
        $item = Get-Item -LiteralPath $recordPath -ErrorAction Stop
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint -or $item.Length -gt 16384) { return '' }
        $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
        if ($record.schema -ne 2) { return '' }
        return [string]$record.runtime_pin_sha256
    } catch { return '' }
}

function Set-RyoshuSetupState {
    # schema 2: base (Python/libraries/OCR) completion plus the separately reported local
    # inference preparation for the current source pin. Inference may be 'failed' while the
    # base is complete: manual ledger/CSV must keep working without the model.
    param([string]$TaskRoot, [string]$RequirementsFile, $PythonStatus,
          [ValidateSet('pending','complete')][string]$State,
          [string]$RuntimePinSha256 = '',
          [ValidateSet('pending','complete','failed')][string]$InferenceState = 'pending',
          [string]$InferenceReason = '')
    $runtime = Join-Path $TaskRoot 'runtime'
    New-Item -ItemType Directory -Path $runtime -Force | Out-Null
    $recordPath = Join-Path $runtime 'setup-state.json'
    if ((Get-Item -LiteralPath $runtime).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { throw '準備状態の保存先にリンクは使えません。' }
    if ((Test-Path -LiteralPath $recordPath) -and ((Get-Item -LiteralPath $recordPath).Attributes -band [System.IO.FileAttributes]::ReparsePoint)) { throw '準備状態の記録にリンクは使えません。' }
    if ($InferenceReason -notmatch '^[a-z_]{0,40}$') { $InferenceReason = 'install_failed' }
    $record = @{schema=2;state=$State;requirements_sha256=(Get-RyoshuFileSha256 -LiteralPath $RequirementsFile);
                python_version=[string]$PythonStatus.version;python_base=[string]$PythonStatus.base_prefix;
                runtime_pin_sha256=$RuntimePinSha256;inference_state=$InferenceState;inference_reason=$InferenceReason;
                updated_at=(Get-Date).ToString('o')}
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
        # schema 1 (Ollama-era) records are deliberately incomplete: setup resumes and
        # reuses a healthy Python environment while preparing the pinned local runtime.
        return ($record.schema -eq 2 -and $record.state -ceq 'complete' -and
                $record.requirements_sha256 -ceq (Get-RyoshuFileSha256 -LiteralPath $RequirementsFile) -and
                $record.python_version -ceq [string]$PythonStatus.version -and $record.python_base -ieq [string]$PythonStatus.base_prefix)
    } catch { return $false }
}
