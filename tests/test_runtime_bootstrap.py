"""Run the Windows bootstrap against isolated native, process and network fixtures."""

import json
from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell")
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not POWERSHELL, reason="Windows PowerShell bootstrap"
)


def run_ps(tmp_path, script, extra_env=None):
    """Use a child shell and a JSON result file, including scripts which print hosts."""
    fixture = tmp_path / "fixture.ps1"
    result_file = tmp_path / "result.json"
    fixture.write_bytes(b"\xef\xbb\xbf" + script.replace("\n", "\r\n").encode("utf-8"))
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(fixture)],
        env={
            **os.environ,
            # A pytest session launched from pwsh 7 inherits its incompatible
            # Utility module before Windows PowerShell's own script modules.
            "PSModulePath": str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/Modules"),
            "RYOSHU_TEST_RESULT": str(result_file),
            **(extra_env or {}),
        },
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        result.stdout.decode("utf-8", errors="replace")
        + result.stderr.decode("utf-8", errors="replace")
    )
    return json.loads(result_file.read_text("utf-8-sig"))


@pytest.fixture(scope="module")
def native_python(tmp_path_factory):
    """An actual exe emits child-loader failures or executes the real probe code.

    The controlled Python runner patches version/module data before executing
    the helper's own probe. No Ryoshu modules or user data are opened.
    """
    folder = tmp_path_factory.mktemp("native-python-fixture")
    exe = folder / "python.exe"
    runner = folder / "python_fixture.py"
    runner.write_text(
        r'''
import base64
import builtins
import collections
import json
import os
from pathlib import Path
import sys
import types

mode = os.environ.get("RYOSHU_TEST_NATIVE_MODE", "healthy")
arguments = base64.b64decode(os.environ["RYOSHU_TEST_NATIVE_ARGS"]).decode("utf-8").split("\0")
with open(os.environ["RYOSHU_TEST_CALL_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"mode": mode, "argv": arguments,
                          "exe": os.environ["RYOSHU_TEST_NATIVE_EXE"]}) + "\n")
if mode == "ollama" and arguments == ["pull", "qwen3:4b"]:
    raise SystemExit(42 if os.environ.get("RYOSHU_TEST_FAIL_STAGE") == "ollama" else 0)
if "-c" not in arguments:
    if arguments and Path(arguments[0]).name == "open_session.py":
        data_dir = arguments[arguments.index("--data-dir") + 1]
        assert Path(data_dir).resolve() == Path(os.environ["RYOSHU_TEST_DATA_DIR"]).resolve()
        print(json.dumps({"app": "receipt-ledger", "app_version": "fixture",
                          "instance": "fixture-instance", "pid": 77777701,
                          "data_dir": data_dir, "source_dir": os.environ["RYOSHU_TEST_SOURCE_DIR"],
                          "url": "http://127.0.0.1:8766/fixture-session"}))
        raise SystemExit(0)
    if arguments and Path(arguments[0]).name == "prepare_ocr.py":
        raise SystemExit(41 if os.environ.get("RYOSHU_TEST_FAIL_STAGE") == "ocr" else 0)
    raise RuntimeError("Unexpected fixture invocation: " + repr(arguments))
index = arguments.index("-c")
code = arguments[index + 1]
sys.argv = ["-c"] + arguments[index + 2:]
VersionInfo = collections.namedtuple("version_info", "major minor micro releaselevel serial")
version = {"old": (3, 12, 14), "wrong-minor": (3, 13, 0), "future-patch": (3, 12, 16)}.get(mode, (3, 12, 15))
sys.version_info = VersionInfo(*version, "final", 0)
sys.version = ".".join(map(str, version)) + " (native fixture)"
sys.maxsize = 2**31 - 1 if mode == "32bit" else 2**63 - 1
cryptography = types.ModuleType("cryptography")
cryptography.__version__ = "49.0.0" if mode == "old-cryptography" else "50.0.2"
sqlcipher = types.ModuleType("sqlcipher3")
sqlcipher.sqlite_version_info = (3, 53, 3) if mode == "old-sqlcipher" else (3, 53, 4)
sqlcipher.sqlite_version = ".".join(map(str, sqlcipher.sqlite_version_info))
sys.modules["cryptography"] = cryptography
sys.modules["sqlcipher3"] = sqlcipher
if mode == "missing-library":
    original_import = builtins.__import__
    def controlled_import(name, *args, **kwargs):
        if name == "cryptography":
            raise ModuleNotFoundError("No module named 'cryptography' (fixture)")
        return original_import(name, *args, **kwargs)
    builtins.__import__ = controlled_import
exec(compile(code, "<ryoshu-probe>", "exec"), {"__name__": "__main__"})
'''.lstrip(),
        encoding="utf-8",
    )
    compiler = r'''
$ErrorActionPreference = 'Stop'
$source = @'
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
public static class PythonFixture {
    public static int Main(string[] args) {
        string executable = Assembly.GetExecutingAssembly().Location;
        string modeFile = executable + ".mode";
        string mode = File.Exists(modeFile) ? File.ReadAllText(modeFile).Trim() : "healthy";
        if (mode == "broken-child") {
            Console.Error.WriteLine("No Python at 'C:\\fixture-missing-child\\python.exe': uv trampoline child executable is missing");
            return 103;
        }
        if (mode == "malformed") { Console.WriteLine("unexpected native output (not JSON)"); return 0; }
        if (mode == "stderr-warning") { Console.Error.WriteLine("fixture native warning"); }
        var start = new ProcessStartInfo();
        start.FileName = Environment.GetEnvironmentVariable("RYOSHU_TEST_HOST_PYTHON");
        start.UseShellExecute = false;
        start.Arguments = "\"" + Environment.GetEnvironmentVariable("RYOSHU_TEST_PYTHON_RUNNER") + "\"";
        start.EnvironmentVariables["RYOSHU_TEST_NATIVE_ARGS"] = Convert.ToBase64String(Encoding.UTF8.GetBytes(String.Join("\0", args)));
        start.EnvironmentVariables["RYOSHU_TEST_NATIVE_MODE"] = mode;
        start.EnvironmentVariables["RYOSHU_TEST_NATIVE_EXE"] = executable;
        using (var child = Process.Start(start)) { child.WaitForExit(); return child.ExitCode; }
    }
}
'@
Add-Type -TypeDefinition $source -OutputAssembly $env:RYOSHU_TEST_NATIVE_TARGET -OutputType ConsoleApplication
@{compiled=$true} | ConvertTo-Json | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    run_ps(folder, compiler, {"RYOSHU_TEST_NATIVE_TARGET": str(exe)})
    return {"exe": exe, "runner": runner}


def native_env(native_python, tmp_path):
    return {
        "RYOSHU_TEST_HOST_PYTHON": sys.executable,
        "RYOSHU_TEST_PYTHON_RUNNER": str(native_python["runner"]),
        "RYOSHU_TEST_CALL_LOG": str(tmp_path / "python-calls.jsonl"),
        "RYOSHU_TEST_LIBRARY": str(ROOT / "scripts/python-runtime.ps1"),
    }


def fake_python(native_python, target, mode="healthy"):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(native_python["exe"], target)
    Path(str(target) + ".mode").write_text(mode, encoding="ascii")
    return target


STATUS_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
. $env:RYOSHU_TEST_LIBRARY
$failure = $null
$status = $null
try { $status = Get-RyoshuPythonStatus -PythonExe $env:RYOSHU_TEST_PYTHON -RequireLibraries:($env:RYOSHU_TEST_REQUIRE_LIBRARIES -ne '0') }
catch { $failure = $_.Exception.Message }
@{failure=$failure;status=$status;preference=[string]$ErrorActionPreference} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''


@pytest.mark.parametrize("mode", ["missing", "broken-child", "invalid-executable", "malformed"])
def test_status_captures_unusable_native_interpreter_without_escaping(tmp_path, native_python, mode):
    python = tmp_path / "venv/Scripts/python.exe"
    if mode == "invalid-executable":
        python.parent.mkdir(parents=True)
        python.write_bytes(b"this is not a Windows executable")
    elif mode != "missing":
        fake_python(native_python, python, mode)
    result = run_ps(tmp_path, STATUS_SCRIPT, {
        **native_env(native_python, tmp_path), "RYOSHU_TEST_PYTHON": str(python),
    })
    assert result["failure"] is None
    assert result["preference"] == "Stop"
    status = result["status"]
    assert status["exists"] is (mode != "missing")
    assert not status["ready"] and not status["compatible"]
    assert status["reason"] == {"missing": "missing", "broken-child": "launch_failed",
                                "invalid-executable": "launch_failed", "malformed": "invalid_probe"}[mode]
    if mode == "broken-child":
        assert not status["runnable"]
        assert "fixture-missing-child" in status["detail"]
        assert "uv trampoline child executable is missing" in status["detail"]
    elif mode == "malformed":
        assert "unexpected native output" in status["detail"]


@pytest.mark.parametrize("mode,compatible,ready", [
    ("healthy", True, True),
    ("future-patch", True, True),
    ("stderr-warning", True, True),
    ("old", False, False),
    ("wrong-minor", False, False),
    ("32bit", False, False),
    ("old-cryptography", True, False),
    ("old-sqlcipher", True, False),
    ("missing-library", True, False),
])
def test_status_distinguishes_supported_runtime_from_library_readiness(
    tmp_path, native_python, mode, compatible, ready
):
    python = fake_python(native_python, tmp_path / "venv/Scripts/python.exe", mode)
    result = run_ps(tmp_path, STATUS_SCRIPT, {
        **native_env(native_python, tmp_path), "RYOSHU_TEST_PYTHON": str(python),
    })
    assert result["failure"] is None
    assert result["preference"] == "Stop"
    status = result["status"]
    assert status["exists"] and status["runnable"]
    assert status["compatible"] is compatible
    assert status["ready"] is ready
    if not ready:
        assert status["reason"] == ("dependencies_missing" if compatible else "unsupported_python")
    else:
        assert status["reason"] == "ready"
    if mode == "old":
        assert "3.12.14" in str(status["version"])


def test_python_only_probe_can_repair_libraries_without_replacing_compatible_interpreter(tmp_path, native_python):
    python = fake_python(native_python, tmp_path / "venv/Scripts/python.exe", "missing-library")
    result = run_ps(tmp_path, STATUS_SCRIPT, {
        **native_env(native_python, tmp_path), "RYOSHU_TEST_PYTHON": str(python),
        "RYOSHU_TEST_REQUIRE_LIBRARIES": "0",
    })
    assert result["failure"] is None
    assert result["status"]["compatible"] and result["status"]["ready"]
    assert result["status"]["reason"] == "ready"


PROCESS_FIXTURES = r'''
$global:stopped = @()
$global:networkCalls = @()
function global:Get-CimInstance {
    [CmdletBinding()]param($ClassName, $Filter)
    $rows = @($env:RYOSHU_TEST_PROCESSES | ConvertFrom-Json)
    if ($Filter -match 'ProcessId\s*=\s*(\d+)') {
        return @($rows | Where-Object { [int]$_.ProcessId -eq [int]$Matches[1] })
    }
    return $rows
}
function global:Get-NetTCPConnection {
    [CmdletBinding()]param($LocalPort, $State)
    if ($env:RYOSHU_TEST_LISTENER -ne 'none') {
        [PSCustomObject]@{OwningProcess=77777701;LocalAddress='127.0.0.1';LocalPort=$LocalPort;State='Listen'}
    }
}
function global:Invoke-RestMethod {
    [CmdletBinding()]param($Uri, $TimeoutSec, $Method)
    $global:networkCalls += [string]$Uri
    if ([string]$Uri -like 'http://127.0.0.1:11434/*') { return @{version='fixture'} }
    if ($env:RYOSHU_TEST_LISTENER -eq 'ryoshu') {
        return [PSCustomObject]@{app='receipt-ledger';instance='fixture-instance';app_version='fixture'}
    }
    if ($env:RYOSHU_TEST_LISTENER -eq 'foreign') { return [PSCustomObject]@{app='unrelated-service'} }
    throw 'fixture: no health response'
}
function global:Stop-Process {
    [CmdletBinding()]param($Id, $InputObject)
    $global:stopped += [int]$Id
    throw 'fixture: stopping processes is forbidden'
}
function global:Start-Process {
    [CmdletBinding()]param($FilePath, $ArgumentList, $WindowStyle)
    throw 'fixture: starting processes is forbidden'
}
'''


def seeded_task_root(tmp_path):
    profile = tmp_path / "profile 日本語 '&"
    task_root = profile / "StudioYebisu/ReceiptLedger"
    preserved = {
        "data/ledger.sqlite3": b"protected-ledger-fixture\x00",
        "data/originals/receipt.png": b"receipt-original-fixture",
        "data/.protection.json": b"protection-fixture",
        "restored/previous/data/ledger.sqlite3": b"restored-ledger-fixture",
        "cache/ocr/weights.bin": b"ocr-model-fixture",
        "runtime/data-location.json": b'{"data_dir":"retained fixture"}',
        "model-license-acceptance.json": b'{"license":"fixture-accepted"}',
    }
    for name, contents in preserved.items():
        path = task_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    return profile, task_root, preserved


def assert_data_preserved(task_root, preserved):
    for name, contents in preserved.items():
        assert (task_root / name).read_bytes() == contents, name


@pytest.mark.parametrize("process_kind", ["none", "venv", "managed", "sibling-prefix"])
def test_idle_guard_checks_process_paths_without_stopping_anything(tmp_path, process_kind):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    executables = {
        "venv": task_root / "venv/Scripts/python.exe",
        "managed": task_root / "python/cpython-3.12.15-windows-x86_64-none/python.exe",
        "sibling-prefix": Path(str(task_root) + "-other") / "venv/Scripts/python.exe",
    }
    processes = [] if process_kind == "none" else [{
        "ProcessId": 77777701,
        "ParentProcessId": 77777700,
        "ExecutablePath": str(executables[process_kind]),
        "CommandLine": "python -c fixture_background_work",
        "Name": "python.exe",
    }]
    script = r'''
$ErrorActionPreference = 'Stop'
. $env:RYOSHU_TEST_LIBRARY
''' + PROCESS_FIXTURES + r'''
$failure = $null
try { Assert-RyoshuRuntimeIdle -TaskRoot $env:RYOSHU_TEST_TASK_ROOT }
catch { $failure = $_.Exception.Message }
@{error=$failure;stopped=@($global:stopped)} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    result = run_ps(tmp_path, script, {
        "USERPROFILE": str(profile),
        "RYOSHU_TEST_LIBRARY": str(ROOT / "scripts/python-runtime.ps1"),
        "RYOSHU_TEST_TASK_ROOT": str(task_root),
        "RYOSHU_TEST_LISTENER": "none",
        "RYOSHU_TEST_PROCESSES": json.dumps(processes),
    })
    assert result["stopped"] == []
    assert bool(result["error"]) is (process_kind in {"venv", "managed"})
    assert_data_preserved(task_root, preserved)


@pytest.mark.parametrize("listener", ["foreign", "ryoshu", "unresponsive"])
def test_idle_guard_identifies_listener_without_claiming_foreign_app_is_ryoshu(tmp_path, listener):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    script = r'''
$ErrorActionPreference = 'Stop'
. $env:RYOSHU_TEST_LIBRARY
''' + PROCESS_FIXTURES + r'''
$failure = $null
try { Assert-RyoshuRuntimeIdle -TaskRoot $env:RYOSHU_TEST_TASK_ROOT }
catch { $failure = $_.Exception.Message }
@{error=$failure;stopped=@($global:stopped);network=@($global:networkCalls)} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    result = run_ps(tmp_path, script, {
        "USERPROFILE": str(profile),
        "RYOSHU_TEST_LIBRARY": str(ROOT / "scripts/python-runtime.ps1"),
        "RYOSHU_TEST_TASK_ROOT": str(task_root),
        "RYOSHU_TEST_LISTENER": listener,
        "RYOSHU_TEST_PROCESSES": "[]",
    })
    assert result["stopped"] == []
    assert result["error"]
    if listener == "ryoshu":
        assert "Ryoshuが起動中" in result["error"]
    elif listener == "foreign":
        assert "別のアプリ" in result["error"]
        assert "停止.cmd" not in result["error"]
    assert any("/api/health" in uri for uri in result["network"])
    assert_data_preserved(task_root, preserved)


def setup_source_fixture(tmp_path, native_python):
    """Copy only launcher files and replace the network bootstrap in the copy."""
    source = tmp_path / "source 日本語 '&"
    scripts = source / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "setup.ps1", source / "setup.ps1")
    for name in ["python-runtime.ps1", "launcher-process.ps1"]:
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    (source / "requirements.lock.txt").write_text("# fixture: never installed\n", encoding="ascii")
    (scripts / "bootstrap-uv.ps1").write_text(
        "function Get-RyoshuUv { param([string]$Directory) return $env:RYOSHU_TEST_UV }\n",
        encoding="utf-8-sig",
    )
    uv = tmp_path / "uv-fixture.ps1"
    uv.write_text(r'''
$ErrorActionPreference = 'Stop'
$arguments = @($args)
$arguments | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:RYOSHU_TEST_UV_LOG -Encoding UTF8
if ($arguments[0] -eq 'python' -and $arguments[1] -eq 'install') {
    if ($arguments[2] -ne '3.12.15') { throw 'fixture: unpinned interpreter requested' }
    $installIndex = [array]::IndexOf($arguments, '--install-dir')
    if ($installIndex -lt 0) { throw 'fixture: interpreter installation is not app-owned' }
    $target = Join-Path $arguments[$installIndex+1] 'cpython-3.12.15-windows-x86_64-none\python.exe'
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath $env:RYOSHU_TEST_NATIVE_TEMPLATE -Destination $target
    [IO.File]::WriteAllText(($target + '.mode'), 'healthy')
} elseif ($arguments[0] -eq 'venv') {
    $pythonIndex = [array]::IndexOf($arguments, '--python')
    if ($pythonIndex -lt 0) { throw 'fixture: managed interpreter missing' }
    $managed = $arguments[$pythonIndex+1]
    $expected = Join-Path $env:USERPROFILE 'StudioYebisu\ReceiptLedger\python\cpython-3.12.15-windows-x86_64-none\python.exe'
    if ($managed -ine $expected) { throw 'fixture: outside interpreter requested' }
    $target = Join-Path $arguments[-1] 'Scripts\python.exe'
    New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
    Copy-Item -LiteralPath $managed -Destination $target
    [IO.File]::WriteAllText(($target + '.mode'), 'healthy')
} elseif ($arguments[0] -eq 'pip' -and $arguments[1] -eq 'install') {
    if ($arguments -notcontains '--require-hashes') { throw 'fixture: unverified dependencies requested' }
    $pythonIndex = [array]::IndexOf($arguments, '--python')
    [IO.File]::WriteAllText(($arguments[$pythonIndex+1] + '.mode'), 'healthy')
} else { throw ('fixture: unexpected uv operation: ' + ($arguments -join ' ')) }
$global:LASTEXITCODE = 0
'''.lstrip(), encoding="utf-8-sig")
    tools = tmp_path / "fake-tools"
    fake_python(native_python, tools / "ollama.exe", "ollama")
    return source, uv, tools


def run_setup_fixture(tmp_path, native_python, profile, listener="none", processes=None, failure_stage=None):
    source, uv, tools = setup_source_fixture(tmp_path, native_python)
    script = r'''
$ErrorActionPreference = 'Stop'
''' + PROCESS_FIXTURES + r'''
$failure = $null
$messages = @()
try { $messages = @(& $env:RYOSHU_TEST_SETUP -AcceptModelLicense 6>&1 3>&1 | ForEach-Object { [string]$_ }) }
catch { $failure = $_.Exception.Message }
@{error=$failure;stopped=@($global:stopped);messages=$messages} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    result = run_ps(tmp_path, script, {
        **native_env(native_python, tmp_path),
        "USERPROFILE": str(profile),
        "PATH": str(tools) + os.pathsep + os.environ["PATH"],
        "RYOSHU_TEST_SETUP": str(source / "setup.ps1"),
        "RYOSHU_TEST_UV": str(uv),
        "RYOSHU_TEST_UV_LOG": str(tmp_path / "uv-calls.jsonl"),
        "RYOSHU_TEST_NATIVE_TEMPLATE": str(native_python["exe"]),
        "RYOSHU_TEST_LISTENER": listener,
        "RYOSHU_TEST_PROCESSES": json.dumps(processes or []),
        "RYOSHU_TEST_FAIL_STAGE": failure_stage or "",
    })
    uv_log = tmp_path / "uv-calls.jsonl"
    calls = [json.loads(line.lstrip("\ufeff")) for line in uv_log.read_text("utf-8-sig").splitlines()] if uv_log.exists() else []
    return result, calls


@pytest.mark.parametrize("mode", ["broken-child", "old", "missing-executable", "healthy"])
def test_setup_preserves_old_environment_and_data_or_reuses_healthy_runtime(tmp_path, native_python, mode):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    # A successful explicit setup records license acceptance; every ledger,
    # restored original, cache and data-location record must remain unchanged.
    preserved.pop("model-license-acceptance.json")
    venv = task_root / "venv"
    marker = venv / "Lib/site-packages/retained-fixture.txt"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"old-runtime-do-not-delete")
    old_executable = None
    if mode != "missing-executable":
        python = fake_python(native_python, venv / "Scripts/python.exe", mode)
        old_executable = python.read_bytes()
    result, calls = run_setup_fixture(tmp_path, native_python, profile)
    assert result["error"] is None, result
    assert result["stopped"] == []
    assert_data_preserved(task_root, preserved)
    backups = list(task_root.glob("venv-before-update-*"))
    rebuild = mode != "healthy"
    assert len(backups) == int(rebuild)
    if rebuild:
        assert (backups[0] / marker.relative_to(venv)).read_bytes() == b"old-runtime-do-not-delete"
        if old_executable is not None:
            assert (backups[0] / "Scripts/python.exe").read_bytes() == old_executable
            assert (backups[0] / "Scripts/python.exe.mode").read_text("ascii") == mode
        assert not marker.exists()
        assert [call[:2] for call in calls] == [["python", "install"], ["venv", "--python"], ["pip", "install"]]
        installed = calls[0]
        assert installed[2] == "3.12.15"
        assert installed[installed.index("--install-dir") + 1] == str(task_root / "python")
    else:
        assert marker.read_bytes() == b"old-runtime-do-not-delete"
        assert [call[:2] for call in calls] == [["pip", "install"]]
    assert (venv / "Scripts/python.exe").exists()


@pytest.mark.parametrize("busy", ["foreign", "ryoshu", "venv-process", "managed-process"])
def test_setup_does_not_move_or_replace_runtime_while_busy(tmp_path, native_python, busy):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    python = fake_python(native_python, task_root / "venv/Scripts/python.exe", "broken-child")
    before = python.read_bytes()
    processes = []
    if busy.endswith("-process"):
        executable = python if busy == "venv-process" else task_root / "python/cpython-3.12.15-windows-x86_64-none/python.exe"
        processes = [{"ProcessId": 77777701, "ExecutablePath": str(executable),
                      "ParentProcessId": 77777700, "CommandLine": "python -c fixture", "Name": "python.exe"}]
    result, calls = run_setup_fixture(
        tmp_path, native_python, profile,
        listener=busy if busy in {"foreign", "ryoshu"} else "none", processes=processes,
    )
    assert result["error"]
    assert result["stopped"] == [] and calls == []
    if busy == "foreign":
        assert "別のアプリ" in result["error"] and "停止.cmd" not in result["error"]
    elif busy == "ryoshu":
        assert "Ryoshuが起動中" in result["error"]
    assert_data_preserved(task_root, preserved)
    assert python.read_bytes() == before
    assert Path(str(python) + ".mode").read_text("ascii") == "broken-child"
    assert list(task_root.glob("venv-before-update-*")) == []


def test_setup_preserves_broken_managed_base_before_reinstalling_it(tmp_path, native_python):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    preserved.pop("model-license-acceptance.json")
    fake_python(native_python, task_root / "venv/Scripts/python.exe", "broken-child")
    managed_root = task_root / "python/cpython-3.12.15-windows-x86_64-none"
    managed = fake_python(native_python, managed_root / "python.exe", "broken-child")
    marker = managed_root / "Lib/retained-base-fixture.txt"
    marker.parent.mkdir()
    marker.write_bytes(b"managed-base-before-repair")
    before = managed.read_bytes()
    result, calls = run_setup_fixture(tmp_path, native_python, profile)
    assert result["error"] is None, result
    assert result["stopped"] == []
    assert_data_preserved(task_root, preserved)
    backups = list(managed_root.parent.glob(managed_root.name + "-before-update-*"))
    assert len(backups) == 1
    assert (backups[0] / "python.exe").read_bytes() == before
    assert (backups[0] / "python.exe.mode").read_text("ascii") == "broken-child"
    assert (backups[0] / marker.relative_to(managed_root)).read_bytes() == b"managed-base-before-repair"
    assert not marker.exists()
    assert Path(str(managed) + ".mode").read_text("ascii") == "healthy"
    assert calls[0][:3] == ["python", "install", "3.12.15"]
    assert "--no-bin" in calls[0] and "--no-registry" in calls[0]


@pytest.mark.parametrize("condition", [
    "missing", "pending", "complete", "changed-lock", "missing-lock", "changed-version",
    "changed-base", "invalid-json", "partial-marker", "wrong-schema",
])
def test_setup_completion_requires_matching_complete_record(tmp_path, condition):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    requirements = tmp_path / "requirements.lock.txt"
    original_lock = b"fixture-dependency==1.0.0 --hash=sha256:fixture\n"
    requirements.write_bytes(original_lock)
    base = task_root / "python/cpython-3.12.15-windows-x86_64-none"
    script = r'''
$ErrorActionPreference = 'Stop'
. $env:RYOSHU_TEST_LIBRARY
$status = [PSCustomObject]@{ready=$true;version='3.12.15';base_prefix=$env:RYOSHU_TEST_BASE}
$statePath = Join-Path $env:RYOSHU_TEST_TASK_ROOT 'runtime\setup-state.json'
if ($env:RYOSHU_TEST_CONDITION -ne 'missing') {
    $state = if ($env:RYOSHU_TEST_CONDITION -eq 'pending') { 'pending' } else { 'complete' }
    Set-RyoshuSetupState -TaskRoot $env:RYOSHU_TEST_TASK_ROOT -RequirementsFile $env:RYOSHU_TEST_REQUIREMENTS -PythonStatus $status -State $state | Out-Null
}
switch ($env:RYOSHU_TEST_CONDITION) {
    'changed-lock' { Add-Content -LiteralPath $env:RYOSHU_TEST_REQUIREMENTS -Value 'new fixture dependency' }
    'missing-lock' { Remove-Item -LiteralPath $env:RYOSHU_TEST_REQUIREMENTS }
    'changed-version' { $status.version = '3.12.16' }
    'changed-base' { $status.base_prefix += '-different' }
    'invalid-json' { [IO.File]::WriteAllText($statePath, '{truncated') }
    'partial-marker' { @{schema=1;state='complete'} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8 }
    'wrong-schema' {
        $record = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $record.schema = 99
        $record | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    }
}
$complete = Test-RyoshuSetupComplete -TaskRoot $env:RYOSHU_TEST_TASK_ROOT -RequirementsFile $env:RYOSHU_TEST_REQUIREMENTS -PythonStatus $status
$raw = if (Test-Path -LiteralPath $statePath) { [IO.File]::ReadAllText($statePath) } else { $null }
@{complete=$complete;raw=$raw} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    result = run_ps(tmp_path, script, {
        "USERPROFILE": str(profile),
        "RYOSHU_TEST_LIBRARY": str(ROOT / "scripts/python-runtime.ps1"),
        "RYOSHU_TEST_TASK_ROOT": str(task_root),
        "RYOSHU_TEST_REQUIREMENTS": str(requirements),
        "RYOSHU_TEST_BASE": str(base),
        "RYOSHU_TEST_CONDITION": condition,
    })
    assert result["complete"] is (condition == "complete")
    if condition in {"pending", "complete"}:
        marker = json.loads(result["raw"])
        assert marker["schema"] == 1
        assert marker["state"] == condition
        assert marker["requirements_sha256"].lower() == sha256(original_lock).hexdigest()
        assert marker["python_version"] == "3.12.15"
        assert marker["python_base"] == str(base)
    assert_data_preserved(task_root, preserved)


@pytest.mark.parametrize("failure_stage", ["ocr", "ollama"])
def test_interrupted_model_preparation_stays_pending_until_successful_retry(tmp_path, native_python, failure_stage):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    python = fake_python(native_python, task_root / "venv/Scripts/python.exe")
    marker = task_root / "runtime/setup-state.json"
    first, first_calls = run_setup_fixture(tmp_path, native_python, profile, failure_stage=failure_stage)
    assert first["error"]
    assert ("文字読み取りモデル" if failure_stage == "ocr" else "AIモデル") in first["error"]
    assert first["stopped"] == []
    assert [call[:2] for call in first_calls] == [["pip", "install"]]
    pending = json.loads(marker.read_text("utf-8-sig"))
    assert pending["state"] == "pending"
    assert_data_preserved(task_root, preserved)

    # The runtime already passes its Python/library probe after pip succeeded.
    # Model preparation still needs a second setup, with the pending marker
    # replaced by complete only once both external preparation stages succeed.
    status = run_ps(tmp_path, STATUS_SCRIPT, {
        **native_env(native_python, tmp_path), "RYOSHU_TEST_PYTHON": str(python),
    })
    assert status["status"]["ready"]
    retried, calls = run_setup_fixture(tmp_path, native_python, profile)
    assert retried["error"] is None, retried
    complete = json.loads(marker.read_text("utf-8-sig"))
    assert complete["schema"] == 1 and complete["state"] == "complete"
    assert complete["python_version"] == "3.12.15"
    assert complete["python_base"] == status["status"]["base_prefix"]
    lock = tmp_path / "source 日本語 '&/requirements.lock.txt"
    assert complete["requirements_sha256"].lower() == sha256(lock.read_bytes()).hexdigest()
    assert [call[:2] for call in calls] == [["pip", "install"], ["pip", "install"]]
    assert not list(task_root.glob("venv-before-update-*"))
    assert_data_preserved(task_root, preserved)


def run_start_fixture(tmp_path, native_python, profile, *, existing=False, ensure_setup=True, explicit_data=False):
    """Exercise copied start.ps1 with a fake listener/server and no session secrets."""
    source = tmp_path / "start-source 日本語 '&"
    scripts = source / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "start.ps1", source / "start.ps1")
    for name in ["python-runtime.ps1", "launcher-process.ps1"]:
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    (source / "requirements.lock.txt").write_text("# start fixture only\n", encoding="ascii")
    package = source / "receipt_ledger"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("__version__ = 'fixture'\n", encoding="ascii")
    (source / "setup.ps1").write_text("throw 'fixture: unexpected setup invocation'\n", encoding="ascii")
    task_root = profile / "StudioYebisu/ReceiptLedger"
    python = task_root / "venv/Scripts/python.exe"
    script = r'''
$ErrorActionPreference = 'Stop'
. (Join-Path $env:RYOSHU_TEST_SOURCE_DIR 'scripts\python-runtime.ps1')
if ($env:RYOSHU_TEST_ENSURE_SETUP -eq '1') {
    $status = Get-RyoshuPythonStatus -PythonExe $env:RYOSHU_TEST_PYTHON -RequireLibraries
    Set-RyoshuSetupState -TaskRoot $env:RYOSHU_TEST_TASK_ROOT -RequirementsFile (Join-Path $env:RYOSHU_TEST_SOURCE_DIR 'requirements.lock.txt') -PythonStatus $status -State complete
}
$global:serverActive = $env:RYOSHU_TEST_EXISTING -eq '1'
$global:serverStarts = 0
function global:Get-CimInstance {
    [CmdletBinding()]param($ClassName, $Filter)
    if ($global:serverActive) {
        [PSCustomObject]@{ProcessId=77777701;ParentProcessId=77777700;ExecutablePath=$env:RYOSHU_TEST_PYTHON;CommandLine='python -m receipt_ledger';Name='python.exe'}
    }
}
function global:Get-NetTCPConnection {
    [CmdletBinding()]param($LocalPort, $State)
    if ($global:serverActive) { [PSCustomObject]@{OwningProcess=77777701;LocalPort=$LocalPort} }
}
function global:Invoke-RestMethod {
    [CmdletBinding()]param($Uri, $TimeoutSec)
    if ([string]$Uri -like 'http://127.0.0.1:11434/*') { return @{version='fixture'} }
    if ($global:serverActive -and [string]$Uri -eq 'http://127.0.0.1:8766/api/health') {
        return @{app='receipt-ledger';instance='fixture-instance';app_version='fixture'}
    }
    throw 'fixture: no running server'
}
function global:Start-Process {
    [CmdletBinding()]param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$PassThru, $RedirectStandardOutput, $RedirectStandardError)
    if ($FilePath -ine $env:RYOSHU_TEST_PYTHON -or ($ArgumentList -join ' ') -ne '-m receipt_ledger') {
        throw 'fixture: unexpected child process requested'
    }
    $global:serverStarts += 1
    $global:serverActive = $true
    [PSCustomObject]@{Id=77777701;HasExited=$false}
}
function global:Start-Sleep { param($Milliseconds) }
function global:Stop-Process { [CmdletBinding()]param($Id) throw 'fixture: stopping processes is forbidden' }
$failure = $null
$messages = @()
$arguments = @{NoBrowser=$true}
if ($env:RYOSHU_TEST_EXPLICIT_DATA -eq '1') { $arguments.DataDir = $env:RYOSHU_TEST_DATA_DIR }
try { $messages = @(& (Join-Path $env:RYOSHU_TEST_SOURCE_DIR 'start.ps1') @arguments 6>&1 | ForEach-Object { [string]$_ }) }
catch { $failure = $_.Exception.Message }
@{error=$failure;starts=$global:serverStarts;messages=$messages} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    return run_ps(tmp_path, script, {
        **native_env(native_python, tmp_path),
        "USERPROFILE": str(profile),
        "RYOSHU_TEST_TASK_ROOT": str(task_root),
        "RYOSHU_TEST_PYTHON": str(python),
        "RYOSHU_TEST_SOURCE_DIR": str(source),
        "RYOSHU_TEST_DATA_DIR": str(task_root / "data"),
        "RYOSHU_TEST_EXISTING": "1" if existing else "0",
        "RYOSHU_TEST_ENSURE_SETUP": "1" if ensure_setup else "0",
        "RYOSHU_TEST_EXPLICIT_DATA": "1" if explicit_data else "0",
    })


@pytest.mark.parametrize("ledger_file", [None, "ledger.sqlite3", "ledger.enc.sqlite3"])
def test_first_start_reuse_and_restart_pin_only_established_ledger(tmp_path, native_python, ledger_file):
    profile = tmp_path / "start-profile 日本語 '&"
    task_root = profile / "StudioYebisu/ReceiptLedger"
    data = task_root / "data"
    data.mkdir(parents=True)
    fake_python(native_python, task_root / "venv/Scripts/python.exe")
    if ledger_file:
        (data / ledger_file).write_bytes(b"existing-ledger-must-not-be-opened")
    location = task_root / "runtime/data-location.json"

    first = run_start_fixture(tmp_path, native_python, profile)
    assert first["error"] is None, first
    assert first["starts"] == 1
    assert location.exists() is bool(ledger_file)
    if ledger_file:
        assert json.loads(location.read_text("utf-8-sig"))["data_dir"] == str(data)

    # Existing healthy servers remain usable while no completed setup marker
    # is present, including the first-run protection screen before a DB exists.
    (task_root / "runtime/setup-state.json").unlink()
    reused = run_start_fixture(tmp_path, native_python, profile, existing=True, ensure_setup=False)
    assert reused["error"] is None, reused
    assert reused["starts"] == 0
    assert location.exists() is bool(ledger_file)

    restarted = run_start_fixture(tmp_path, native_python, profile)
    assert restarted["error"] is None, restarted
    assert restarted["starts"] == 1
    assert location.exists() is bool(ledger_file)
    if ledger_file:
        assert (data / ledger_file).read_bytes() == b"existing-ledger-must-not-be-opened"
        assert json.loads(location.read_text("utf-8-sig"))["data_dir"] == str(data)
    else:
        assert list(data.iterdir()) == []


@pytest.mark.parametrize("explicit_data", [False, True])
def test_recorded_or_explicit_missing_ledger_is_rejected_before_server_start(tmp_path, native_python, explicit_data):
    profile = tmp_path / "missing-profile"
    task_root = profile / "StudioYebisu/ReceiptLedger"
    data = task_root / "data"
    data.mkdir(parents=True)
    original = data / "original-receipt.png"
    original.write_bytes(b"existing-original-must-stay")
    fake_python(native_python, task_root / "venv/Scripts/python.exe")
    location = task_root / "runtime/data-location.json"
    location.parent.mkdir()
    recorded = json.dumps({"data_dir": str(data)}).encode("utf-8")
    if not explicit_data:
        location.write_bytes(recorded)
    result = run_start_fixture(tmp_path, native_python, profile, explicit_data=explicit_data)
    assert result["error"] and "選択した帳簿が見つかりません" in result["error"]
    assert result["starts"] == 0
    assert original.read_bytes() == b"existing-original-must-stay"
    assert sorted(path.name for path in data.iterdir()) == [original.name]
    if not explicit_data:
        assert location.read_bytes() == recorded
    else:
        assert not location.exists()
