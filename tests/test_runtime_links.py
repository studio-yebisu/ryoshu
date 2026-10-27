"""Real Windows junction/symlink refusal and cleanup-on-failure regressions (no writes into link targets)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from receipt_ledger import runtime as runtime_module
from receipt_ledger import runtime_win
from receipt_ledger.runtime import GenerateRequest, LlamaRuntime, RuntimeFailure
from receipt_ledger.runtime_pin import ready_state
from runtime_fakes import FakeChildRuntime, make_pin, write_fake_server

windows_only = pytest.mark.skipif(os.name != "nt", reason="junctions and Job objects are Windows features; Ryoshu targets Windows")
REQUEST = GenerateRequest("system", "receipt text", {"type": "object"})


def junction(link, target):
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def listing(folder):
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


def no_spawn(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "_spawn", lambda *a: pytest.fail("no process may start"))


@windows_only
@pytest.mark.parametrize("where", ["root", "ancestor"])
def test_junction_runtime_root_or_ancestor_is_refused_not_resolved(tmp_path, monkeypatch, where):
    pin_path, real_root = make_pin(tmp_path / "real")
    before = listing(real_root)
    if where == "root":
        root = tmp_path / "linked-root"
        junction(root, real_root)
    else:
        junction(tmp_path / "linked-parent", real_root.parent)
        root = tmp_path / "linked-parent" / real_root.name
    runtime = LlamaRuntime(pin_path, root)
    assert runtime.root == root  # the raw path is kept, not the target
    no_spawn(runtime, monkeypatch)
    assert ready_state(pin_path, root)["reason_code"] == "runtime_integrity"
    assert runtime.status()["model_ready"] is False
    with pytest.raises(RuntimeFailure) as caught:
        runtime.generate(REQUEST)
    runtime.close()
    assert caught.value.code == "runtime_integrity"
    assert listing(real_root) == before


@windows_only
def test_symlink_runtime_root_is_refused(tmp_path, monkeypatch):
    pin_path, real_root = make_pin(tmp_path / "real")
    root = tmp_path / "symlink-root"
    try:
        os.symlink(real_root, root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation needs Developer Mode or privilege on this account: {exc}")
    runtime = LlamaRuntime(pin_path, root)
    no_spawn(runtime, monkeypatch)
    with pytest.raises(RuntimeFailure) as caught:
        runtime.generate(REQUEST)
    assert caught.value.code == "runtime_integrity"


@windows_only
def test_config_runtime_root_keeps_the_raw_linked_path(tmp_path):
    target = tmp_path / "target"
    link = tmp_path / "link"
    junction(link, target)
    workspace = str(Path(__file__).resolve().parents[1])
    code = "import sys;sys.path.insert(0,sys.argv[1]);from receipt_ledger import config;print(config.RUNTIME_ROOT)"
    env = {**os.environ, "RECEIPT_LEDGER_RUNTIME_ROOT": str(link)}
    out = subprocess.run([sys.executable, "-I", "-B", "-c", code, workspace], env=env, capture_output=True, text=True, check=True)
    assert Path(out.stdout.strip()) == link


@windows_only
def test_junction_in_runtime_tmp_is_refused_before_any_outside_write(tmp_path, monkeypatch):
    pin_path, root = make_pin(tmp_path / "rt")
    outside = tmp_path / "outside"
    (root / "runtime").mkdir()
    junction(root / "runtime" / "tmp", outside)
    runtime = LlamaRuntime(pin_path, root)
    no_spawn(runtime, monkeypatch)
    with pytest.raises(RuntimeFailure) as caught:
        runtime.generate(REQUEST)
    runtime.close()
    assert caught.value.code == "runtime_integrity" and listing(outside) == []


@windows_only
def test_junction_in_owner_records_stops_child_and_writes_nothing_outside(tmp_path):
    pin_path, root = make_pin(tmp_path / "rt")
    outside = tmp_path / "outside"
    (root / "runtime").mkdir()
    junction(root / "runtime" / "owners", outside)
    runtime = FakeChildRuntime(pin_path, root, script=write_fake_server(tmp_path))
    with pytest.raises(RuntimeFailure) as caught:
        runtime.generate(REQUEST)
    assert caught.value.code == "runtime_integrity" and listing(outside) == []
    assert len(runtime.launches) == 1 and runtime._proc is None
    runtime.close()


@windows_only
def test_forced_owner_record_failure_cleans_up_child_immediately(tmp_path, monkeypatch):
    pin_path, root = make_pin(tmp_path / "rt")
    runtime = FakeChildRuntime(pin_path, root, script=write_fake_server(tmp_path))
    spawned = []
    real_spawn = runtime._spawn
    monkeypatch.setattr(runtime, "_spawn", lambda *a: spawned.append(real_spawn(*a)) or spawned[-1])
    def broken_replace(*args):
        raise OSError("disk full (forced)")
    monkeypatch.setattr(runtime_module.os, "replace", broken_replace)
    with pytest.raises(RuntimeFailure) as caught:
        runtime.generate(REQUEST)
    assert caught.value.code == "runtime_integrity"
    assert spawned and spawned[0].poll() is not None and not runtime_win.running(spawned[0].pid)
    assert runtime._proc is None and runtime._job is None
    assert list((root / "runtime" / "owners").iterdir()) == []
    runtime.close()


@windows_only
def test_unlisted_binary_after_verified_start_refuses_restart_before_spawn(tmp_path):
    pin_path, root = make_pin(tmp_path / "rt")
    runtime = FakeChildRuntime(pin_path, root, script=write_fake_server(tmp_path))
    try:
        runtime.generate(REQUEST)
        runtime._stop_child()
        (root / "llama" / "synthetic-cpu" / "planted.dll").write_bytes(b"MZ")
        with pytest.raises(RuntimeFailure) as caught:
            runtime.generate(REQUEST)
        assert caught.value.code == "runtime_integrity" and len(runtime.launches) == 1
    finally:
        runtime.close()
