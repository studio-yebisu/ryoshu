"""Early PowerShell callers refuse a linked task root/ancestor BEFORE any mkdir/lock/download (fake inputs only).

Every link points to a NEW private target with a sentinel; the target tree must stay byte-identical. Links are
removed with os.rmdir (link only) before tmp cleanup. No real profile, no network, no native process.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# Adopted Kimi copy for path.link_refused (scripts/python-runtime.ps1 Assert-RyoshuUnlinkedPath).
LINK_REFUSED = "保存先またはその上位フォルダーにリンクが使われているため、自動で書き込みません。リンクではない通常のフォルダーを保存先に選び直してください。"
pytestmark = pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell"), reason="Windows PowerShell")


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): (p.read_bytes() if p.is_file() else None) for p in sorted(folder.rglob("*"))}


@pytest.fixture
def outside(tmp_path):
    target = tmp_path / "outside-target 外部"
    target.mkdir()
    (target / "SENTINEL.txt").write_bytes(b"must-stay-untouched\n")
    links = []

    def link(at, kind="junction"):
        at.parent.mkdir(parents=True, exist_ok=True)
        if kind == "junction":
            import _winapi
            _winapi.CreateJunction(str(target), str(at))
        else:
            try:
                os.symlink(target, at, target_is_directory=True)
            except OSError:
                pytest.skip("symlink privilege not held")
        links.append(at)
        return at

    before = snapshot(target)
    yield target, link, before
    for at in reversed(links):
        try:
            os.rmdir(at)  # removes only the link
        except OSError:
            pass
    assert snapshot(target) == before


def run_ps(tmp_path, script, env):
    helper = tmp_path / "guard-fixture.ps1"
    helper.write_bytes(b"\xef\xbb\xbf" + script.replace("\n", "\r\n").encode("utf-8"))
    out = tmp_path / "guard-result.json"
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(helper)],
                       env={**os.environ, **env, "RYOSHU_TEST_RESULT": str(out)}, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", errors="replace")
    return json.loads(out.read_text("utf-8-sig"))


STUBS = r'''
function global:Invoke-WebRequest { throw 'fixture: network forbidden' }
function global:Invoke-RestMethod { throw 'fixture: network forbidden' }
function global:Start-Process { throw 'fixture: process start forbidden' }
function global:Stop-Process { throw 'fixture: process stop forbidden' }
function global:Get-CimInstance { return @() }
function global:Get-NetTCPConnection { return @() }
function global:Read-Host { return 'N' }
'''


@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_setup_refuses_linked_task_root_before_lock_or_record(tmp_path, outside, kind):
    target, link, before = outside
    source = tmp_path / "source 日本語 '&"
    (source / "scripts").mkdir(parents=True)
    shutil.copyfile(ROOT / "setup.ps1", source / "setup.ps1")
    for n in ("python-runtime.ps1", "launcher-process.ps1", "bootstrap-uv.ps1"):
        shutil.copyfile(ROOT / "scripts" / n, source / "scripts" / n)
    profile = tmp_path / "profile 日本語 space"
    link(profile / "StudioYebisu" / "ReceiptLedger", kind)
    script = STUBS + r'''
$failure = $null
try { & (Join-Path $env:RYOSHU_TEST_SOURCE 'setup.ps1') | Out-Null } catch { $failure = $_.Exception.Message }
@{error=$failure} | ConvertTo-Json | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    res = run_ps(tmp_path, script, {"USERPROFILE": str(profile), "RYOSHU_TEST_SOURCE": str(source)})
    assert res["error"] == LINK_REFUSED
    assert snapshot(target) == before  # no runtime\, start.lock or license record through the link


@pytest.mark.parametrize("where", ["root", "ancestor", "runtime-itself"])
def test_startup_lock_refuses_links_before_creating_anything(tmp_path, outside, where):
    target, link, before = outside
    base = tmp_path / "profile 日本語"
    if where == "root":
        root = link(base / "StudioYebisu" / "ReceiptLedger")
    elif where == "ancestor":
        root = link(base / "StudioYebisu") / "ReceiptLedger"
    else:
        root = base / "StudioYebisu" / "ReceiptLedger"
        root.mkdir(parents=True)
        link(root / "runtime")
    script = r'''
. $env:RYOSHU_TEST_LIBRARY
$failure = $null
try { $lock = Open-RyoshuStartupLock (Join-Path $env:RYOSHU_TEST_ROOT 'runtime'); $lock.Dispose() } catch { $failure = $_.Exception.Message }
@{error=$failure} | ConvertTo-Json | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    res = run_ps(tmp_path, script, {"RYOSHU_TEST_LIBRARY": str(ROOT / "scripts/launcher-process.ps1"), "RYOSHU_TEST_ROOT": str(root)})
    assert res["error"] == LINK_REFUSED
    assert snapshot(target) == before


def test_uv_bootstrap_refuses_linked_ancestor_before_creating_tools_dir(tmp_path, outside):
    target, link, before = outside
    root = link(tmp_path / "profile 日本語" / "ReceiptLedger")
    script = STUBS + r'''
. $env:RYOSHU_TEST_BOOTSTRAP
$failure = $null
try { Get-RyoshuUv (Join-Path $env:RYOSHU_TEST_ROOT 'tools\uv-0.13.5') | Out-Null } catch { $failure = $_.Exception.Message }
@{error=$failure} | ConvertTo-Json | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    res = run_ps(tmp_path, script, {"RYOSHU_TEST_BOOTSTRAP": str(ROOT / "scripts/bootstrap-uv.ps1"), "RYOSHU_TEST_ROOT": str(root)})
    assert res["error"] == LINK_REFUSED
    assert snapshot(target) == before  # tools\uv-0.13.5 not created through the linked ancestor


def test_unlinked_japanese_space_relative_paths_are_accepted(tmp_path):
    root = tmp_path / "正常 path with space" / "ReceiptLedger"
    script = r'''
. $env:RYOSHU_TEST_LIBRARY
Set-Location -LiteralPath $env:RYOSHU_TEST_BASE
$lock = Open-RyoshuStartupLock '.\正常 path with space\ReceiptLedger\runtime'
$lock.Dispose()
Assert-RyoshuUnlinkedPath (Join-Path $env:RYOSHU_TEST_BASE 'not-yet-existing\deeper')
@{ok=$true} | ConvertTo-Json | Set-Content -LiteralPath $env:RYOSHU_TEST_RESULT -Encoding UTF8
'''
    res = run_ps(tmp_path, script, {"RYOSHU_TEST_LIBRARY": str(ROOT / "scripts/launcher-process.ps1"), "RYOSHU_TEST_BASE": str(tmp_path)})
    assert res == {"ok": True}
    assert (root / "runtime" / "start.lock").is_file()
