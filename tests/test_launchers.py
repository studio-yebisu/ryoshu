"""PowerShell launcher control with fake processes only; never stops a real app."""
import json
import base64
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name != 'nt' or not shutil.which('powershell'), reason='Windows PowerShell launcher')
@pytest.mark.parametrize('ledger_file', ['ledger.sqlite3', 'ledger.enc.sqlite3'])
def test_restored_ledger_launcher_passes_literal_japanese_paths_and_never_overwrites(tmp_path, ledger_file):
    source = tmp_path / "日本語 ' & %PATH% source"
    data = tmp_path / "帳簿 ' & %PATH% data"
    output = tmp_path / 'restored'
    for folder in [source, data, output]:
        folder.mkdir()
    (data / ledger_file).write_bytes(b'fixture-not-opened')
    (source / 'start.ps1').write_text('param([string]$DataDir)\n[System.IO.File]::WriteAllText($env:RYOSHU_TEST_RESULT,$DataDir)\n', 'utf-8')
    result_file = tmp_path / 'received.txt'
    script = r'''
$ErrorActionPreference = 'Stop'
. $env:RYOSHU_TEST_LIBRARY
$launcher = New-RyoshuLedgerLauncher -SourceDir $env:RYOSHU_TEST_SOURCE -DataDir $env:RYOSHU_TEST_DATA -OutputDir $env:RYOSHU_TEST_OUTPUT
$before = [System.IO.File]::ReadAllText($launcher)
$rejected = $false
try { New-RyoshuLedgerLauncher -SourceDir $env:RYOSHU_TEST_SOURCE -DataDir $env:RYOSHU_TEST_DATA -OutputDir $env:RYOSHU_TEST_OUTPUT }
catch { $rejected = $true }
if (-not $rejected -or [System.IO.File]::ReadAllText($launcher) -cne $before) { throw 'existing launcher was overwritten' }
'''
    run_ps_fixture(tmp_path, script, {'RYOSHU_TEST_LIBRARY': str(ROOT/'scripts/launcher-process.ps1'),
        'RYOSHU_TEST_SOURCE': str(source), 'RYOSHU_TEST_DATA': str(data), 'RYOSHU_TEST_OUTPUT': str(output)})
    launcher = output / 'この帳簿を開く.cmd'
    text = launcher.read_text('ascii')
    encoded = text.split('-EncodedCommand ', 1)[1].strip()
    decoded = base64.b64decode(encoded).decode('utf-16-le')
    assert str(data).replace("'", "''") in decoded
    result = subprocess.run(['powershell', '-NoProfile', '-EncodedCommand', encoded],
        env={**os.environ, 'RYOSHU_TEST_RESULT': str(result_file)}, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode('utf-8', errors='replace')
    assert result_file.read_text('utf-8-sig') == str(data)


@pytest.mark.skipif(os.name != 'nt' or not shutil.which('powershell'), reason='Windows PowerShell launcher')
def test_restored_ledger_launcher_rejects_missing_database_without_changing_restored_files(tmp_path):
    source = tmp_path / 'source'
    data = tmp_path / 'restored-data'
    output = tmp_path / 'launchers'
    for folder in [source, data, output]:
        folder.mkdir()
    (source / 'start.ps1').write_text('param([string]$DataDir)\n', 'ascii')
    original = data / 'receipt-original.png'
    original.write_bytes(b'restored-original-do-not-touch')
    script = r'''
$ErrorActionPreference = 'Stop'
. $env:RYOSHU_TEST_LIBRARY
$failure = $null
try { New-RyoshuLedgerLauncher -SourceDir $env:RYOSHU_TEST_SOURCE -DataDir $env:RYOSHU_TEST_DATA -OutputDir $env:RYOSHU_TEST_OUTPUT }
catch { $failure = $_.Exception.Message }
if (-not $failure -or $failure -notmatch '帳簿が見つかりません') { throw 'missing restored database was not rejected' }
'''
    run_ps_fixture(tmp_path, script, {
        'RYOSHU_TEST_LIBRARY': str(ROOT / 'scripts/launcher-process.ps1'),
        'RYOSHU_TEST_SOURCE': str(source), 'RYOSHU_TEST_DATA': str(data), 'RYOSHU_TEST_OUTPUT': str(output),
    })
    assert list(output.iterdir()) == []
    assert original.read_bytes() == b'restored-original-do-not-touch'


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"), reason="Windows PowerShell launcher")
@pytest.mark.parametrize("foreign", [False, True])
def test_stop_checks_all_unique_listener_owners_before_any_stop(tmp_path, foreign):
    profile = tmp_path / "profile"
    runtime = profile / "StudioYebisu/ReceiptLedger/runtime"
    runtime.mkdir(parents=True)
    record = runtime / "server.json"
    record.write_text(json.dumps({"pid": 77777701, "python": "fake-python.exe"}), "utf-8")
    output = tmp_path / "result.json"
    helper = tmp_path / "verify.ps1"
    script = r'''
$ErrorActionPreference = 'Stop'
$global:stopped = @()
function global:Get-CimInstance {
    [CmdletBinding()]param($ClassName,$Filter)
    $taskId = [int]($Filter -replace 'ProcessId=','')
    [PSCustomObject]@{ProcessId=$taskId;ExecutablePath='fake-python.exe';CommandLine='fake-python -m receipt_ledger';ParentProcessId=$(if($taskId -eq 77777703){99}else{77777701})}
}
function global:Get-NetTCPConnection {
    [CmdletBinding()]param($LocalPort,$State)
    [PSCustomObject]@{OwningProcess=77777702}
    [PSCustomObject]@{OwningProcess=77777702}
    if($env:RYOSHU_TEST_FOREIGN -eq '1'){[PSCustomObject]@{OwningProcess=77777703}}
}
function global:Get-Process {[CmdletBinding()]param($Id) [PSCustomObject]@{Id=$Id}}
function global:Stop-Process {[CmdletBinding()]param($Id) $global:stopped += [int]$Id}
$failure = $null
try { & $env:RYOSHU_TEST_STOP } catch { $failure = $_.Exception.Message }
@{stopped=@($global:stopped);error=$failure;record_exists=(Test-Path -LiteralPath $env:RYOSHU_TEST_RECORD)} | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    helper.write_bytes(b"\xef\xbb\xbf" + script.replace("\n", "\r\n").encode("utf-8"))
    env = {**os.environ, "USERPROFILE": str(profile), "RYOSHU_TEST_STOP": str(ROOT / "stop.ps1"),
           "RYOSHU_TEST_RECORD": str(record), "RYOSHU_TEST_RESULT": str(output),
           "RYOSHU_TEST_FOREIGN": "1" if foreign else "0"}
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper)],
                            env=env, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    observed = json.loads(output.read_text("utf-8-sig"))
    if foreign:
        assert observed["stopped"] == []
        assert observed["record_exists"] and "別のアプリ" in observed["error"]
    else:
        assert observed["stopped"] == [77777702, 77777701]
        assert not observed["record_exists"] and observed["error"] is None


def run_ps_fixture(tmp_path, script, extra_env=None):
    helper = tmp_path / "fixture.ps1"
    helper.write_bytes(b"\xef\xbb\xbf" + script.replace("\n", "\r\n").encode("utf-8"))
    result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper)],
                            env={**os.environ, **(extra_env or {})}, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"), reason="Windows PowerShell launcher")
def test_startup_lock_prevents_second_launch_and_releases_cleanly(tmp_path):
    script = r'''
$ErrorActionPreference = 'Stop'
. $env:RYOSHU_TEST_LIBRARY
$first = Open-RyoshuStartupLock $env:RYOSHU_TEST_RUNTIME
$blocked = $false
try {
    try { $second = Open-RyoshuStartupLock $env:RYOSHU_TEST_RUNTIME; $second.Dispose() }
    catch { $blocked = $_.Exception.Message -match '起動・終了処理が進行中' }
} finally { $first.Dispose() }
if (-not $blocked) { throw 'second launch was not blocked' }
$after = Open-RyoshuStartupLock $env:RYOSHU_TEST_RUNTIME
$after.Dispose()
'''
    run_ps_fixture(tmp_path, script, {"RYOSHU_TEST_LIBRARY": str(ROOT / "scripts/launcher-process.ps1"),
                                    "RYOSHU_TEST_RUNTIME": str(tmp_path / "runtime")})


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"), reason="Windows PowerShell launcher")
@pytest.mark.parametrize("stale", [False, True])
def test_stop_can_recover_verified_app_without_a_current_record(tmp_path, stale):
    profile = tmp_path / "profile"
    runtime = profile / "StudioYebisu/ReceiptLedger/runtime"
    runtime.mkdir(parents=True)
    record = runtime / "server.json"
    if stale:
        record.write_text(json.dumps({"pid": 77777703, "python": "old-python.exe"}), "utf-8")
    script = r'''
$ErrorActionPreference = 'Stop'
$global:stopped = @()
function global:Get-CimInstance {
    [CmdletBinding()]param($ClassName,$Filter)
    $taskId = [int]($Filter -replace 'ProcessId=','')
    if ($taskId -eq 77777703) { return $null }
    $executable = if ($taskId -eq 77777701) {Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger\venv\Scripts\python.exe'} else {'base-python.exe'}
    [PSCustomObject]@{ProcessId=$taskId;ExecutablePath=$executable;CommandLine='python -m receipt_ledger';ParentProcessId=77777701}
}
function global:Get-NetTCPConnection {[CmdletBinding()]param($LocalPort,$State) [PSCustomObject]@{OwningProcess=77777702}}
function global:Stop-Process {[CmdletBinding()]param($Id) $global:stopped += [int]$Id}
& $env:RYOSHU_TEST_STOP
if ($global:stopped.Count -ne 1 -or $global:stopped[0] -ne 77777702) { throw 'wrong process target' }
if (Test-Path -LiteralPath $env:RYOSHU_TEST_RECORD) { throw 'stale record was not removed' }
'''
    run_ps_fixture(tmp_path, script, {"USERPROFILE": str(profile), "RYOSHU_TEST_STOP": str(ROOT / "stop.ps1"),
                                    "RYOSHU_TEST_RECORD": str(record)})
