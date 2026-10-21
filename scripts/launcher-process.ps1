function Open-RyoshuStartupLock {
    param([string]$RuntimeDir)
    New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    try { return [System.IO.File]::Open((Join-Path $RuntimeDir 'start.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch [System.IO.IOException] { throw 'Ryoshuの起動・終了処理が進行中です。少し待ってから、もう一度操作してください。' }
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
        if (-not $owned) { throw '接続先を別のアプリが使っているため、自動操作を止めました。別のアプリは停止していません。' }
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
