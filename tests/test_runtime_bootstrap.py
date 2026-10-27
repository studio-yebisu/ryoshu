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
# Adopted Kimi copy shown by setup.ps1 (replaces the former [RYOSHU <slot>] technical markers).
CRT_UNAVAILABLE = "Microsoftの配布物または利用条件を準備できませんでした。ネット接続と空き容量を確認し、「初回セットアップ.cmd」で再試行してください。帳簿は変更していません。理由："
CRT_TERMS_PROMPT = "上記のMicrosoft利用条件に同意し、必要なDLLをRyoshu専用の保存先へ配置しますか？ [y/N]"
CRT_DECLINED = "Microsoft利用条件に同意しなかったため、OCRとAIによる科目候補の準備を行いませんでした。帳簿への手入力とCSV出力は利用できます。あとで同意するか選び直すには「初回セットアップ.cmd」をもう一度実行してください。"
CRT_PREVIOUSLY_DECLINED = "前回はMicrosoft利用条件への同意を見送ったため、今回も条件を確認してから同意するかを選択できます。"
CRT_TERMS_IDENTITY_FIRST = "EULA ID・条件のSHA-256・対象バージョン："
INFERENCE_NOT_READY = "OCRとAIによる科目候補の準備が完了していません。あとで「初回セットアップ.cmd」を実行してください。帳簿への手入力とCSV出力は利用できます。"
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
if mode == "ollama-forbidden":
    raise SystemExit(99)  # setup/start must never invoke Ollama; the call log above records any attempt
if "-c" not in arguments:
    if arguments and Path(arguments[0]).name == "prepare_llama.py" and "--crt-only" in arguments:
        root = Path(arguments[arguments.index("--root") + 1])
        assert arguments[arguments.index("--python-base") + 1], "CRT step needs the managed interpreter directory"
        crt_mode = os.environ.get("RYOSHU_TEST_CRT", "ok")  # ok | pending-missing | pending-system
        eula = "e" * 64
        stock = crt_mode in ("pending-missing", "prior-declined")  # stock PC: no machine-wide VC++ runtime
        closure = {"msvcp140.dll": "missing" if stock else "system32",
                   "msvcp140_1.dll": "missing" if stock else "system32",
                   "vcruntime140.dll": "python_local", "vcruntime140_1.dll": "python_local"}
        if os.environ.get("RYOSHU_TEST_FAIL_STAGE") == "crt":
            result, stage = {"ok": False, "reason_code": "crt_unavailable"}, "crt"
        elif crt_mode == "ok":
            result, stage = {"ok": True, "reason_code": ""}, "crt"
        elif "--accept-crt-terms" in arguments:
            assert arguments[arguments.index("--accept-crt-terms") + 1] == eula, "acceptance must name the shown terms"
            result, stage = {"ok": True, "reason_code": ""}, "crt-accept"
        elif "--decline-crt-terms" in arguments:
            result, stage = {"ok": False, "reason_code": "crt_declined", "closure": closure}, "crt-decline"
        elif crt_mode == "prior-declined" and "--reconsider-crt-terms" not in arguments:
            result, stage = {"ok": False, "reason_code": "crt_declined", "closure": closure}, "crt-kept-decline"
        else:
            terms = root / "crt-terms-fixture.txt"
            terms.parent.mkdir(parents=True, exist_ok=True)
            terms.write_text("MICROSOFT SOFTWARE LICENSE TERMS (fixture)\n", encoding="utf-8")
            result = {"ok": False, "reason_code": "crt_terms_pending", "terms_text": str(terms), "eula_id": "Cpp_2015-2022_ENU.1033",
                      "eula_sha256": eula, "version": "14.44.35211.0", "package_url": "https://download.visualstudio.microsoft.com/x",
                      "package_sha256": "c" * 64, "closure": closure,
                      "previous_decision": "declined" if crt_mode == "prior-declined" else "none"}
            stage = "crt-pending"
        (root / "runtime").mkdir(parents=True, exist_ok=True)
        (root / "runtime/crt-setup-result.json").write_text(json.dumps(result), encoding="utf-8")
        with open(os.environ["RYOSHU_TEST_CALL_LOG"], "a", encoding="utf-8") as log:
            log.write(json.dumps({"mode": "stage", "argv": [stage], "exe": "stage"}) + "\n")
        raise SystemExit(0 if result["ok"] else 3)
    if arguments and Path(arguments[0]).name == "prepare_llama.py":
        root = Path(arguments[arguments.index("--root") + 1])
        failing = os.environ.get("RYOSHU_TEST_FAIL_STAGE") == "llama"
        with open(os.environ["RYOSHU_TEST_CALL_LOG"], "a", encoding="utf-8") as log:
            log.write(json.dumps({"mode": "stage", "argv": ["llama"], "exe": "stage"}) + "\n")
        (root / "runtime").mkdir(parents=True, exist_ok=True)
        (root / "runtime/llama-setup-result.json").write_text(json.dumps(
            {"ok": not failing, "reason_code": "vc_runtime_missing" if failing else ""}), encoding="utf-8")
        raise SystemExit(3 if failing else 0)
    if arguments and Path(arguments[0]).name == "open_session.py":
        data_dir = arguments[arguments.index("--data-dir") + 1]
        assert Path(data_dir).resolve() == Path(os.environ["RYOSHU_TEST_DATA_DIR"]).resolve()
        print(json.dumps({"app": "receipt-ledger", "app_version": "fixture",
                          "instance": "fixture-instance", "pid": 77777701,
                          "data_dir": data_dir, "source_dir": os.environ["RYOSHU_TEST_SOURCE_DIR"],
                          "url": "http://127.0.0.1:8766/fixture-session"}))
        raise SystemExit(0)
    if arguments and Path(arguments[0]).name == "prepare_ocr.py":
        with open(os.environ["RYOSHU_TEST_CALL_LOG"], "a", encoding="utf-8") as log:
            log.write(json.dumps({"mode": "stage", "argv": ["ocr"], "exe": "stage"}) + "\n")
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
# The probe activates the app-owned CRT before importing native libraries; record the call and its root.
ledger_pkg = types.ModuleType("receipt_ledger")
crt_bootstrap = types.ModuleType("receipt_ledger.crt_bootstrap")
def fixture_activate(root=None, *args, **kwargs):
    with open(os.environ["RYOSHU_TEST_CALL_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps({"mode": "activate", "argv": [str(root), str(sys.modules.get("cryptography") is cryptography)],
                              "exe": "activate"}) + "\n")
    return {"state": "fixture"}
crt_bootstrap.activate = fixture_activate
ledger_pkg.crt_bootstrap = crt_bootstrap
if "crt_bootstrap" in code:  # other -c helpers (e.g. the version read) import the copied source package
    sys.modules["receipt_ledger"] = ledger_pkg
    sys.modules["receipt_ledger.crt_bootstrap"] = crt_bootstrap
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
    # An Ollama on PATH must be ignored entirely (no detection, serve, or pull).
    fake_python(native_python, tools / "ollama.exe", "ollama-forbidden")
    return source, uv, tools


def ollama_invocations(tmp_path):
    log = tmp_path / "python-calls.jsonl"
    if not log.exists():
        return []
    return [c for c in map(json.loads, log.read_text("utf-8").splitlines()) if Path(c["exe"]).name.lower() == "ollama.exe"]


def run_setup_fixture(tmp_path, native_python, profile, listener="none", processes=None, failure_stage=None, crt="ok", answer=None,
                      launched_by_start=False, qa_terms_sha=None):
    source, uv, tools = setup_source_fixture(tmp_path, native_python)
    script = r'''
$ErrorActionPreference = 'Stop'
''' + PROCESS_FIXTURES + r'''
$global:prompts = @()
function global:Read-Host { param($Prompt) $global:prompts += [string]$Prompt; if ($null -eq $env:RYOSHU_TEST_ANSWER) { throw 'fixture: unexpected prompt' }; if ($env:RYOSHU_TEST_ANSWER -eq '<empty>') { return '' }; return $env:RYOSHU_TEST_ANSWER }
''' + r'''
$failure = $null
$messages = @()
$setupArgs = @{AcceptModelLicense=$true}
if ($env:RYOSHU_TEST_FROM_START -eq '1') { $setupArgs.StartupLockHeld = $true }   # as start.ps1 invokes setup
if ($env:RYOSHU_TEST_QA_TERMS) { $setupArgs.QaAcceptCrtTermsSha256 = $env:RYOSHU_TEST_QA_TERMS }
try { $messages = @(& $env:RYOSHU_TEST_SETUP @setupArgs 6>&1 3>&1 | ForEach-Object { [string]$_ }) }
catch { $failure = $_.Exception.Message }
@{error=$failure;stopped=@($global:stopped);messages=$messages;prompts=@($global:prompts)} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    extra = {"RYOSHU_TEST_CRT": crt, "RYOSHU_TEST_FROM_START": "1" if launched_by_start else "0",
             "RYOSHU_TEST_QA_TERMS": qa_terms_sha or ""}
    if answer is not None:
        extra["RYOSHU_TEST_ANSWER"] = answer or "<empty>"  # Windows treats an empty env value as unset
    result = run_ps(tmp_path, script, {
        **extra,
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
    "changed-base", "invalid-json", "partial-marker", "wrong-schema", "ollama-era-schema1",
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
    'partial-marker' { @{schema=2;state='complete'} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8 }
    'ollama-era-schema1' {
        $record = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $record.schema = 1
        $record | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    }
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
        assert marker["schema"] == 2
        assert marker["state"] == condition
        assert marker["requirements_sha256"].lower() == sha256(original_lock).hexdigest()
        assert marker["python_version"] == "3.12.15"
        assert marker["python_base"] == str(base)
    assert_data_preserved(task_root, preserved)


def stage_order(tmp_path):
    log = tmp_path / "python-calls.jsonl"
    return [c["argv"][0] for c in map(json.loads, log.read_text("utf-8").splitlines()) if c["mode"] == "stage"]


def test_crt_closure_runs_before_ocr_and_its_failure_stops_base_setup(tmp_path, native_python):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    fake_python(native_python, task_root / "venv/Scripts/python.exe")
    ok, _ = run_setup_fixture(tmp_path, native_python, profile)
    assert ok["error"] is None, ok
    assert stage_order(tmp_path)[:2] == ["crt", "ocr"]  # onnxruntime is first imported by OCR
    calls = [json.loads(line) for line in (tmp_path / "python-calls.jsonl").read_text("utf-8").splitlines()]
    order = [c["mode"] if c["mode"] in ("activate", "stage") else None for c in calls]
    # the CRT step precedes the library probe, and that probe activates the app-owned CRT for THIS root first
    activations = [c for c in calls if c["mode"] == "activate"]
    assert activations and order.index("stage") < order.index("activate")
    assert all(Path(c["argv"][0]) == task_root for c in activations)
    (tmp_path / "python-calls.jsonl").unlink()
    failed, _ = run_setup_fixture(tmp_path, native_python, profile, failure_stage="crt")
    assert failed["error"] and CRT_UNAVAILABLE + "crt_unavailable" in failed["error"]
    assert stage_order(tmp_path) == ["crt"]  # OCR never attempted without a CRT closure
    assert json.loads((task_root / "runtime/setup-state.json").read_text("utf-8-sig"))["state"] == "pending"
    assert ollama_invocations(tmp_path) == []
    assert_data_preserved(task_root, preserved)


def test_runtime_terms_accept_places_crt_then_prepares_ocr_and_ai(tmp_path, native_python):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    fake_python(native_python, task_root / "venv/Scripts/python.exe")
    res, _ = run_setup_fixture(tmp_path, native_python, profile, crt="pending-missing", answer="y")
    assert res["error"] is None, res
    assert len(res["prompts"]) == 1 and CRT_TERMS_PROMPT in res["prompts"][0]
    assert stage_order(tmp_path) == ["crt-pending", "crt-accept", "ocr", "llama"]
    shown = "\n".join(res["messages"])
    assert "Cpp_2015-2022_ENU.1033" in shown and "MICROSOFT SOFTWARE LICENSE TERMS (fixture)" in shown
    state = json.loads((task_root / "runtime/setup-state.json").read_text("utf-8-sig"))
    assert state["state"] == "complete" and state["inference_state"] == "complete"
    assert_data_preserved(task_root, preserved)


@pytest.mark.parametrize("answer", ["n", "", "yes please"])
def test_runtime_terms_decline_on_stock_pc_keeps_manual_ledger_and_skips_only_native_steps(tmp_path, native_python, answer):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    fake_python(native_python, task_root / "venv/Scripts/python.exe")
    res, _ = run_setup_fixture(tmp_path, native_python, profile, crt="pending-missing", answer=answer)
    assert res["error"] is None, res  # not a crash: the app can still open for manual work
    assert stage_order(tmp_path) == ["crt-pending", "crt-decline"]  # anything but exactly 'y' is a decline
    state = json.loads((task_root / "runtime/setup-state.json").read_text("utf-8-sig"))
    assert state["state"] == "complete" and state["inference_state"] == "failed" and state["inference_reason"] == "crt_declined"
    assert any(CRT_DECLINED in m for m in res["messages"])
    assert ollama_invocations(tmp_path) == []
    assert_data_preserved(task_root, preserved)


@pytest.mark.parametrize("answer,accepted", [("y", True), ("n", False), ("", False)])
def test_prior_decline_explicit_setup_rerun_prompts_again_with_current_terms(tmp_path, native_python, answer, accepted):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    fake_python(native_python, task_root / "venv/Scripts/python.exe")
    res, _ = run_setup_fixture(tmp_path, native_python, profile, crt="prior-declined", answer=answer)
    assert res["error"] is None, res
    assert len(res["prompts"]) == 1 and CRT_TERMS_PROMPT in res["prompts"][0]
    shown = "\n".join(res["messages"])
    assert CRT_PREVIOUSLY_DECLINED in shown and "MICROSOFT SOFTWARE LICENSE TERMS (fixture)" in shown
    calls = [json.loads(line) for line in (tmp_path / "python-calls.jsonl").read_text("utf-8").splitlines()]
    first_crt = next(c for c in calls if "--crt-only" in c["argv"])
    assert "--reconsider-crt-terms" in first_crt["argv"]
    if accepted:
        assert stage_order(tmp_path) == ["crt-pending", "crt-accept", "ocr", "llama"]
    else:
        assert stage_order(tmp_path) == ["crt-pending", "crt-decline"]
        state = json.loads((task_root / "runtime/setup-state.json").read_text("utf-8-sig"))
        assert state["inference_reason"] == "crt_declined"
    assert_data_preserved(task_root, preserved)


def test_qa_terms_flag_accepts_only_the_shown_terms_without_prompting_and_is_marked_synthetic(tmp_path, native_python):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    fake_python(native_python, task_root / "venv/Scripts/python.exe")
    res, _ = run_setup_fixture(tmp_path, native_python, profile, crt="pending-missing", answer=None, qa_terms_sha="e" * 64)
    assert res["error"] is None, res
    assert res["prompts"] == [] and stage_order(tmp_path) == ["crt-pending", "crt-accept", "ocr", "llama"]
    calls = [json.loads(line) for line in (tmp_path / "python-calls.jsonl").read_text("utf-8").splitlines()]
    accept = next(c for c in calls if "--accept-crt-terms" in c["argv"])
    assert "--qa-test-acceptance" in accept["argv"]       # recorded as qa_test_flag, never as a person's choice
    assert any(CRT_TERMS_IDENTITY_FIRST in m for m in res["messages"])   # terms still shown first
    assert not any("qa_test_acceptance" in m for m in res["messages"])     # no console/product-copy marker
    event = json.loads((task_root / "runtime/qa-event-crt-terms.json").read_text("utf-8"))
    assert event == {"schema": 1, "event": "crt_terms_qa_test_acceptance", "method": "qa_test_flag", "eula_sha256": "e" * 64,
                     "version": "14.44.35211.0"}
    assert_data_preserved(task_root, preserved)


def test_qa_terms_flag_naming_other_terms_stops_without_any_decision(tmp_path, native_python):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    fake_python(native_python, task_root / "venv/Scripts/python.exe")
    res, _ = run_setup_fixture(tmp_path, native_python, profile, crt="pending-missing", answer=None, qa_terms_sha="f" * 64)
    assert res["error"] and "crt_terms_mismatch" in res["error"]
    assert res["prompts"] == [] and stage_order(tmp_path) == ["crt-pending"]   # neither accepted nor declined
    assert not (task_root / "runtime/qa-event-crt-terms.json").exists()
    assert_data_preserved(task_root, preserved)


def test_prior_decline_is_kept_without_prompt_when_setup_is_run_by_start(tmp_path, native_python):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    fake_python(native_python, task_root / "venv/Scripts/python.exe")
    res, _ = run_setup_fixture(tmp_path, native_python, profile, crt="prior-declined", answer=None, launched_by_start=True)
    assert res["error"] is None, res      # no prompt (Read-Host would throw), no acceptance
    assert res["prompts"] == [] and stage_order(tmp_path) == ["crt-kept-decline"]
    assert any(CRT_DECLINED in m for m in res["messages"])
    assert_data_preserved(task_root, preserved)


def test_runtime_terms_decline_where_windows_already_provides_crt_continues(tmp_path, native_python):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    fake_python(native_python, task_root / "venv/Scripts/python.exe")
    res, _ = run_setup_fixture(tmp_path, native_python, profile, crt="pending-system", answer="n")
    assert res["error"] is None, res
    assert stage_order(tmp_path) == ["crt-pending", "crt-decline", "ocr", "llama"]
    assert_data_preserved(task_root, preserved)


def test_runtime_terms_prompt_without_an_answer_is_never_acceptance(tmp_path, native_python):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    fake_python(native_python, task_root / "venv/Scripts/python.exe")
    res, _ = run_setup_fixture(tmp_path, native_python, profile, crt="pending-missing", answer=None)
    assert res["error"] and "unexpected prompt" in res["error"]  # non-interactive: stops, no default
    assert "crt-accept" not in stage_order(tmp_path)
    assert_data_preserved(task_root, preserved)


@pytest.mark.parametrize("failure_stage", ["ocr", "llama"])
def test_interrupted_model_preparation_stays_pending_until_successful_retry(tmp_path, native_python, failure_stage):
    profile, task_root, preserved = seeded_task_root(tmp_path)
    python = fake_python(native_python, task_root / "venv/Scripts/python.exe")
    marker = task_root / "runtime/setup-state.json"
    first, first_calls = run_setup_fixture(tmp_path, native_python, profile, failure_stage=failure_stage)
    assert first["stopped"] == []
    assert [call[:2] for call in first_calls] == [["pip", "install"]]
    recorded = json.loads(marker.read_text("utf-8-sig"))
    if failure_stage == "ocr":
        # OCR is required for any reading: base setup stays pending and setup reports the failure.
        assert first["error"] and "文字読み取りモデル" in first["error"]
        assert recorded["state"] == "pending"
    else:
        # Local inference preparation failure must not block the base setup: manual ledger/CSV
        # stay usable, the technical reason is recorded and shown, and nothing is thrown.
        assert first["error"] is None, first
        assert recorded["schema"] == 2 and recorded["state"] == "complete"
        assert recorded["inference_state"] == "failed" and recorded["inference_reason"] == "vc_runtime_missing"
        assert any(INFERENCE_NOT_READY + " [vc_runtime_missing]" in m for m in first["messages"])
    assert ollama_invocations(tmp_path) == []
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
    assert complete["schema"] == 2 and complete["state"] == "complete"
    assert complete["inference_state"] == "complete" and complete["inference_reason"] == ""
    assert ollama_invocations(tmp_path) == []
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


@pytest.mark.parametrize("contents", [b"", b"Ryoshu SHA256 fixture\n", bytes(range(256)) * 8193], ids=["empty", "text", "binary-stream"])
def test_file_hash_uses_literal_paths_and_releases_native_stream_without_filehash_cmdlet(tmp_path, contents):
    target = tmp_path / "hash 日本語 ' &[literal].bin"
    target.write_bytes(contents)
    script = r'''
$ErrorActionPreference = 'Stop'
function global:Get-FileHash { throw 'Get-FileHash must not be used' }
. $env:RYOSHU_TEST_LIBRARY
$hash = Get-RyoshuFileSha256 -LiteralPath $env:RYOSHU_TEST_HASH_FILE
$exclusive = [IO.File]::Open($env:RYOSHU_TEST_HASH_FILE, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
$exclusive.Dispose()
@{hash=$hash;stream_released=$true} | ConvertTo-Json | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    result = run_ps(tmp_path, script, {
        "RYOSHU_TEST_LIBRARY": str(ROOT / "scripts/python-runtime.ps1"),
        "RYOSHU_TEST_HASH_FILE": str(target),
    })
    assert result == {"hash": sha256(contents).hexdigest(), "stream_released": True}
    assert target.read_bytes() == contents


def test_setup_marker_hash_works_after_pwsh7_module_path_breaks_windows_filehash_autoload(tmp_path):
    pwsh_modules = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PowerShell/7/Modules"
    if not (pwsh_modules / "Microsoft.PowerShell.Utility").is_dir():
        pytest.skip("PowerShell 7 Utility module is not installed")
    requirements = tmp_path / "requirements 日本語 [literal].txt"
    contents = b"dependency==1.2.3 --hash=sha256:fixture\n"
    requirements.write_bytes(contents)
    task_root = tmp_path / "profile/StudioYebisu/ReceiptLedger"
    script = r'''
$ErrorActionPreference = 'Stop'
# This mirrors the native launcher loading GUI/.NET support before setup.
Add-Type -TypeDefinition 'public class RyoshuHashModulePathFixture {}'
$legacyAvailable = $null -ne (Get-Command Get-FileHash -ErrorAction SilentlyContinue)
if ($legacyAvailable) { throw 'fixture did not reproduce unavailable Get-FileHash' }
. $env:RYOSHU_TEST_LIBRARY
$status = [PSCustomObject]@{ready=$true;version='3.12.15';base_prefix='fixture-app-owned-python'}
Set-RyoshuSetupState -TaskRoot $env:RYOSHU_TEST_TASK_ROOT -RequirementsFile $env:RYOSHU_TEST_REQUIREMENTS -PythonStatus $status -State pending
$pending = Test-RyoshuSetupComplete -TaskRoot $env:RYOSHU_TEST_TASK_ROOT -RequirementsFile $env:RYOSHU_TEST_REQUIREMENTS -PythonStatus $status
Set-RyoshuSetupState -TaskRoot $env:RYOSHU_TEST_TASK_ROOT -RequirementsFile $env:RYOSHU_TEST_REQUIREMENTS -PythonStatus $status -State complete
$complete = Test-RyoshuSetupComplete -TaskRoot $env:RYOSHU_TEST_TASK_ROOT -RequirementsFile $env:RYOSHU_TEST_REQUIREMENTS -PythonStatus $status
$record = Get-Content -LiteralPath (Join-Path $env:RYOSHU_TEST_TASK_ROOT 'runtime\setup-state.json') -Raw | ConvertFrom-Json
@{legacy_available=$legacyAvailable;pending_accepted=$pending;complete_accepted=$complete;hash=$record.requirements_sha256} | ConvertTo-Json | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    result = run_ps(tmp_path, script, {
        "PSModulePath": str(pwsh_modules) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/Modules"),
        "RYOSHU_TEST_LIBRARY": str(ROOT / "scripts/python-runtime.ps1"),
        "RYOSHU_TEST_TASK_ROOT": str(task_root),
        "RYOSHU_TEST_REQUIREMENTS": str(requirements),
    })
    assert result == {"legacy_available": False, "pending_accepted": False,
                      "complete_accepted": True, "hash": sha256(contents).hexdigest()}


def test_cached_pinned_uv_is_verified_without_get_filehash_or_network(tmp_path):
    source_uv = Path(os.environ["USERPROFILE"]) / "StudioYebisu/ReceiptLedger/tools/uv-0.13.5/uv.exe"
    expected = "f45d1cb9d9140ca6d3f4005fdfc0ebeccf849fd04d1dc85804155cb923f1597b"
    if not source_uv.is_file() or sha256(source_uv.read_bytes()).hexdigest() != expected:
        pytest.skip("Trusted pinned uv is not locally available for a read-only fixture copy")
    cache = tmp_path / "cached uv 日本語 [literal]"
    cache.mkdir()
    cached_uv = cache / "uv.exe"
    shutil.copyfile(source_uv, cached_uv)
    script = r'''
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition 'public class RyoshuCachedUvHashFixture {}'
function global:Get-FileHash { throw 'Get-FileHash must not be used' }
function global:Invoke-WebRequest { throw 'Cached uv fixture must not download anything' }
# Source bootstrap alone: it must find its runtime hash helper itself.
. $env:RYOSHU_TEST_BOOTSTRAP
$selected = Get-RyoshuUv $env:RYOSHU_TEST_UV_CACHE
@{selected=$selected;hash=(Get-RyoshuFileSha256 -LiteralPath $selected)} | ConvertTo-Json | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    result = run_ps(tmp_path, script, {
        "PSModulePath": os.environ.get("PSModulePath", str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/Modules")),
        "RYOSHU_TEST_BOOTSTRAP": str(ROOT / "scripts/bootstrap-uv.ps1"),
        "RYOSHU_TEST_UV_CACHE": str(cache),
    })
    assert result == {"selected": str(cached_uv), "hash": expected}
    assert sha256(cached_uv.read_bytes()).hexdigest() == expected
