function Open-RyoshuStartupLock {
    param([string]$RuntimeDir)
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
