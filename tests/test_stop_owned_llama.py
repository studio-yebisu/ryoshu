"""stop.ps1 + per-app-instance owner records (runtime/owners/*.json): fake processes only."""
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"), reason="Windows PowerShell launcher")

APP_STOPPED = 77777702      # Ryoshu app found on 8766 and stopped by stop.ps1
OTHER_APP = 55500501        # another live Ryoshu instance (e.g. a test app) not being stopped
CHILD = 88800801
T_APP = datetime(2026, 10, 27, 6, 0, 0, 123456, tzinfo=timezone.utc)
T_CHILD = datetime(2026, 10, 27, 6, 0, 5, 654321, tzinfo=timezone.utc)
T_OTHER = datetime(2026, 10, 27, 5, 0, 0, 1, tzinfo=timezone.utc)


EPOCH_1601 = datetime(1601, 1, 1, tzinfo=timezone.utc)


def filetime(dt):
    """Exact Windows FILETIME (100 ns ticks); float timestamps lose precision at this magnitude."""
    d = dt - EPOCH_1601
    return (d.days * 86400 + d.seconds) * 10_000_000 + d.microseconds * 10


SCENARIOS = {
    # name: (record app pid, record app time, child exists?, child time, exe inside llama?, expect_stop, expect_record_kept)
    "owned-child-of-stopped-app": (APP_STOPPED, T_APP, True, T_CHILD, True, True, False),
    "owned-child-of-dead-app": (44400401, T_APP, True, T_CHILD, True, True, False),
    "pid-reused-different-creation": (APP_STOPPED, T_APP, True, datetime(2026, 10, 27, 7, tzinfo=timezone.utc), True, False, False),
    "exe-outside-ryoshu-llama": (APP_STOPPED, T_APP, True, T_CHILD, False, False, True),
    "child-of-other-live-app": (OTHER_APP, T_OTHER, True, T_CHILD, True, False, True),
    "child-already-gone": (APP_STOPPED, T_APP, False, T_CHILD, True, False, False),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_stop_only_kills_verified_owned_llama_children(tmp_path, name):
    app_pid, app_time, child_exists, child_time, inside, expect_stop, expect_kept = SCENARIOS[name]
    profile = tmp_path / "profile 日本語 ' &"
    task = profile / "StudioYebisu/ReceiptLedger"
    owners = task / "runtime/owners"
    owners.mkdir(parents=True)
    exe = (task / "llama/b11700-cpu-x64/llama-server.exe") if inside else (tmp_path / "Tools/llama/llama-server.exe")
    record = owners / f"{app_pid}-{filetime(app_time)}-0123456789abcdef.json"
    record.write_text(json.dumps({"schema": 1, "app_pid": app_pid, "app_creation_time": filetime(app_time),
                                  "runtime_instance": "0123456789abcdef", "child_pid": CHILD,
                                  "child_creation_time": filetime(T_CHILD), "exe_path": str(exe),
                                  "exe_sha256": "0" * 64, "port": 51234, "written_at": "2026-10-27T06:00:05Z"}), "utf-8")
    venv_python = str(task / "venv/Scripts/python.exe")
    processes = {
        str(APP_STOPPED): {"ExecutablePath": "base-python.exe", "CommandLine": "python -m receipt_ledger",
                           "ParentProcessId": 77777701, "CreationDate": T_APP.isoformat()},
        "77777701": {"ExecutablePath": venv_python, "CommandLine": "python -m receipt_ledger", "ParentProcessId": 1,
                     "CreationDate": T_APP.isoformat()},
        str(OTHER_APP): {"ExecutablePath": venv_python, "CommandLine": "python -m receipt_ledger", "ParentProcessId": 1,
                         "CreationDate": T_OTHER.isoformat()},
    }
    if child_exists:
        processes[str(CHILD)] = {"ExecutablePath": str(exe), "CommandLine": "llama-server.exe -m model", "ParentProcessId": app_pid,
                                 "CreationDate": child_time.isoformat()}
    script = r'''
$ErrorActionPreference = 'Stop'
$global:stopped = @()
$global:table = $env:RYOSHU_TEST_PROCESSES | ConvertFrom-Json
function global:Get-CimInstance {
    [CmdletBinding()]param($ClassName,$Filter)
    $id = [string]([int]($Filter -replace 'ProcessId=',''))
    $row = $global:table.$id
    if (-not $row) { return $null }
    [PSCustomObject]@{ProcessId=[int]$id;ExecutablePath=$row.ExecutablePath;CommandLine=$row.CommandLine;
        ParentProcessId=$row.ParentProcessId;CreationDate=[datetime]::Parse($row.CreationDate).ToUniversalTime()}
}
function global:Get-NetTCPConnection {[CmdletBinding()]param($LocalPort,$State) [PSCustomObject]@{OwningProcess=77777702}}
function global:Stop-Process {[CmdletBinding()]param($Id,[switch]$Force,$Name) if ($Name) { throw 'name-based stop' } $global:stopped += [int]$Id}
& $env:RYOSHU_TEST_STOP
@{stopped=@($global:stopped);record=(Test-Path -LiteralPath $env:RYOSHU_TEST_RECORD)} | ConvertTo-Json | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    helper = tmp_path / "fixture.ps1"
    helper.write_bytes(b"\xef\xbb\xbf" + script.replace("\n", "\r\n").encode("utf-8"))
    out = tmp_path / "result.json"
    env = {**os.environ, "USERPROFILE": str(profile), "RYOSHU_TEST_STOP": str(ROOT / "stop.ps1"),
           "RYOSHU_TEST_RECORD": str(record), "RYOSHU_TEST_RESULT": str(out), "RYOSHU_TEST_PROCESSES": json.dumps(processes)}
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(helper)],
                       env=env, capture_output=True, timeout=30)
    assert r.returncode == 0, r.stderr.decode("utf-8", errors="replace")
    observed = json.loads(out.read_text("utf-8-sig"))
    assert APP_STOPPED in observed["stopped"]          # the verified app itself is stopped as before
    assert OTHER_APP not in observed["stopped"]        # another live instance is never touched
    assert (CHILD in observed["stopped"]) is expect_stop
    assert observed["record"] is expect_kept
