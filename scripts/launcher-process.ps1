function Open-RyoshuStartupLock {
    param([string]$RuntimeDir)
    if (-not (Get-Command Assert-RyoshuUnlinkedPath -CommandType Function -ErrorAction SilentlyContinue)) {
        . (Join-Path $PSScriptRoot 'python-runtime.ps1')
    }
    # one lexical absolute path (PowerShell location, no link resolution) for the check, the directory and the lock
    $RuntimeDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($RuntimeDir)
    Assert-RyoshuUnlinkedPath $RuntimeDir   # never create runtime\ or start.lock through a linked root/ancestor
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    try { return [System.IO.File]::Open((Join-Path $RuntimeDir 'start.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch [System.IO.IOException] { throw 'Ryoshuの起動・終了処理が進行中です。前の操作が終わってから、もう一度実行してください。' }
}

function Get-RyoshuListener {
    param([string]$PythonExe, $Record)
    $recordOwner = $null
    if ($Record) {
        $candidate = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$Record.pid) -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.ExecutablePath -ieq $Record.python -and $candidate.CommandLine -match '-m\s+receipt_ledger\b') { $recordOwner = $candidate }
    }
    $listeners = @(Get-NetTCPConnection -LocalPort 8766 -State Listen -ErrorAction SilentlyContinue)
    $owners = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    $result = @()
    foreach ($ownerId in $owners) {
        $server = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$ownerId) -ErrorAction SilentlyContinue
        $owned = $false
        if ($server -and $server.CommandLine -match '-m\s+receipt_ledger\b') {
            if ($recordOwner -and ($server.ProcessId -eq $recordOwner.ProcessId -or $server.ParentProcessId -eq $recordOwner.ProcessId)) { $owned = $true }
            elseif ($server.ExecutablePath -ieq $PythonExe) { $owned = $true }
            else {
                $parent = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$server.ParentProcessId) -ErrorAction SilentlyContinue
                if ($parent -and $parent.ExecutablePath -ieq $PythonExe -and $parent.CommandLine -match '-m\s+receipt_ledger\b') { $owned = $true }
            }
        }
        if (-not $owned) { throw '8766番を別のアプリが使用しています。Ryoshuの起動・停止を中止し、別のアプリは停止していません。接続先の競合を解消してください。' }
        $result += $server
    }
    return $result
}

function ConvertTo-RyoshuMicroseconds {
    # Win32_Process.CreationDate has microsecond precision; owner records store FILETIME (100 ns).
    param($Value)
    if ($Value -is [datetime]) { return [int64][math]::Floor($Value.ToFileTimeUtc() / 10) }
    return [int64][math]::Floor([int64]$Value / 10)
}

function Get-RyoshuOwnedLlamaChildren {
    # Per-app-instance owner records written by receipt_ledger.runtime under runtime\owners.
    # Returns only children that verifiably belong to Ryoshu; never matches by process name.
    param([string]$TaskRoot, [int[]]$StoppingAppIds = @())
    $root = [System.IO.Path]::GetFullPath($TaskRoot).TrimEnd('\')
    $llamaPrefix = (Join-Path $root 'llama') + '\'
    $owners = Join-Path $root 'runtime\owners'
    $found = @()
    if (-not (Test-Path -LiteralPath $owners -PathType Container)) { return $found }
    if ((Get-Item -LiteralPath $owners).Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return $found }
    foreach ($file in @(Get-ChildItem -LiteralPath $owners -Filter '*.json' -File -ErrorAction SilentlyContinue)) {
        if ($file.Attributes -band [System.IO.FileAttributes]::ReparsePoint -or $file.Length -gt 16384) { continue }
        try { $record = Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json } catch { continue }
        if ($record.schema -ne 1 -or -not $record.child_pid -or -not $record.exe_path) { continue }
        $exe = [System.IO.Path]::GetFullPath([string]$record.exe_path)
        if (-not $exe.StartsWith($llamaPrefix, [StringComparison]::OrdinalIgnoreCase)) { continue }
        $child = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$record.child_pid) -ErrorAction SilentlyContinue
        $childMatches = $child -and $child.ExecutablePath -and
            ([System.IO.Path]::GetFullPath($child.ExecutablePath) -ieq $exe) -and
            ((ConvertTo-RyoshuMicroseconds $child.CreationDate) -eq (ConvertTo-RyoshuMicroseconds $record.child_creation_time))
        $app = Get-CimInstance Win32_Process -Filter ('ProcessId=' + [int]$record.app_pid) -ErrorAction SilentlyContinue
        $appAlive = $app -and ((ConvertTo-RyoshuMicroseconds $app.CreationDate) -eq (ConvertTo-RyoshuMicroseconds $record.app_creation_time))
        $found += [PSCustomObject]@{Record=$file.FullName; ChildId=[int]$record.child_pid; ChildMatches=[bool]$childMatches;
            AppAlive=[bool]$appAlive; AppBeingStopped=([int]$record.app_pid -in $StoppingAppIds)}
    }
    return $found
}

function Save-RyoshuServerRecord {
    param([string]$RuntimeDir, $Server, [string]$DataDir, [string]$SourceDir)
    $temporary = Join-Path $RuntimeDir ('server-' + [guid]::NewGuid().ToString('N') + '.tmp')
    $json = @{pid=[int]$Server.ProcessId;python=$Server.ExecutablePath;data_dir=$DataDir;source=$SourceDir;url='http://127.0.0.1:8766'} | ConvertTo-Json
    [System.IO.File]::WriteAllText($temporary, $json, (New-Object System.Text.UTF8Encoding($true)))
    Move-Item -LiteralPath $temporary -Destination (Join-Path $RuntimeDir 'server.json') -Force
}

function New-RyoshuLedgerLauncher {
    param([string]$SourceDir, [string]$DataDir, [string]$OutputDir)
    $startFile = Join-Path ([System.IO.Path]::GetFullPath($SourceDir)) 'start.ps1'
    $ledgerPath = [System.IO.Path]::GetFullPath($DataDir)
    if (-not (Test-Path -LiteralPath $startFile) -or (-not (Test-Path -LiteralPath (Join-Path $ledgerPath 'ledger.sqlite3')) -and -not (Test-Path -LiteralPath (Join-Path $ledgerPath 'ledger.enc.sqlite3')))) {
        throw '起動ファイルか復元済みの帳簿を確認できません。復元は完了していない可能性があります。バックアップからもう一度復元してください。'
    }
    $quotedStart = $startFile.Replace("'", "''")
    $quotedData = $ledgerPath.Replace("'", "''")
    $launchCommand = "`$ErrorActionPreference='Stop'; try { & '$quotedStart' -DataDir '$quotedData' } catch { Write-Host `$_.Exception.Message; Read-Host 'Enterで閉じます'; exit 1 }"
    $encodedCommand = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($launchCommand))
    $launcherPath = Join-Path $OutputDir 'この帳簿を開く.cmd'
    $content = "@echo off`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -EncodedCommand $encodedCommand`r`n"
    $stream = [System.IO.File]::Open($launcherPath, 'CreateNew', 'Write', 'None')
    try {
        $bytes = [System.Text.Encoding]::ASCII.GetBytes($content)
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
    return $launcherPath
}
