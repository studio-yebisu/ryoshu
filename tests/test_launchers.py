"""PowerShell launcher control with fake processes only; never stops a real app."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


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
