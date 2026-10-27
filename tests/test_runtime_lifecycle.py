"""Real Windows process/Job/listener checks using a harmless owned Python HTTP child (no model)."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest

from receipt_ledger import runtime as runtime_module
from receipt_ledger import runtime_win
from receipt_ledger.runtime import GenerateRequest, LlamaRuntime, RuntimeFailure
from receipt_ledger.runtime_pin import load_pin, runtime_paths
from runtime_fakes import FakeChildRuntime, make_pin, write_fake_server

windows_only = pytest.mark.skipif(os.name != "nt", reason="Job objects and the TCP owner table are Windows APIs; Ryoshu targets Windows")
REQUEST = GenerateRequest(system="system text", user="receipt body\n/no_think", schema={"type": "object"})


def alive(pid):
    return runtime_win.running(pid)


def wait_gone(pid, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def assets(tmp_path):
    pin_path, root = make_pin(tmp_path)
    return pin_path, root, write_fake_server(tmp_path)


def test_status_models_and_provision_check_never_spawn(assets, monkeypatch):
    pin_path, root, _ = assets
    runtime = LlamaRuntime(pin_path, root)
    monkeypatch.setattr(runtime, "_spawn", lambda *a: pytest.fail("readiness must not start a process"))
    monkeypatch.setattr(runtime_module, "file_sha256", lambda p: pytest.fail("readiness must not fully hash"))
    assert runtime.provision_check()["model_ok"]
    assert runtime.status() == {**runtime.status(), "state": "stopped", "model_ready": True, "runtime_ready": True}
    assert runtime.models()[0]["id"] == "synthetic/test-model/synthetic.gguf"
    runtime.close()
    assert not (root / "runtime").exists()


def test_close_during_first_verification_is_terminal_and_leaves_no_temp(assets):
    pin_path, root, _ = assets
    entered, release, spawns, errors = threading.Event(), threading.Event(), [], []

    class PausedVerification(LlamaRuntime):
        def _verify_assets(self, *args):
            super()._verify_assets(*args)
            entered.set()
            assert release.wait(10)

        def _spawn(self, *args):
            spawns.append(args)
            raise AssertionError("must never spawn after close")

    runtime = PausedVerification(pin_path, root, idle_stop_s=None)
    def call():
        try:
            runtime.generate(REQUEST)
        except RuntimeFailure as exc:
            errors.append(exc.code)
    worker = threading.Thread(target=call)
    worker.start()
    try:
        assert entered.wait(10)
        started = time.monotonic()
        runtime.close()  # bounded: does not wait for the paused hash
        assert time.monotonic() - started < 5
        assert runtime.status()["state"] == "closed"
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and errors == ["cancelled"] and spawns == []
    final = runtime.status()
    assert final["state"] == "closed" and not final["model_ready"] and not final["runtime_ready"]
    assert not (root / "runtime" / "tmp" / runtime.instance).exists()
    assert not list((root / "runtime" / "owners").glob("*.json")) if (root / "runtime" / "owners").exists() else True


@windows_only
def test_generate_starts_owned_child_with_secret_only_in_env_and_records_ownership(assets):
    pin_path, root, script = assets
    runtime = FakeChildRuntime(pin_path, root, script=script)
    try:
        result = runtime.generate(REQUEST)
        echoed = json.loads(result.content)
        assert echoed["body"]["chat_template_kwargs"] == {"enable_thinking": False}
        assert echoed["body"]["response_format"]["json_schema"]["schema"] == {"type": "object"}
        assert echoed["body"]["messages"][1]["content"] == "receipt body\n/no_think"
        # Python itself may add nothing here; the child sees exactly the allow-list.
        assert {k.upper() for k in echoed["env_keys"]} <= {"LLAMA_API_KEY", "PATH", "TEMP", "TMP", *runtime_module.ENV_ALLOW}
        assert {"LLAMA_API_KEY", "PATH", "SYSTEMROOT", "TEMP", "TMP"} <= {k.upper() for k in echoed["env_keys"]}
        secret = runtime.envs[0]["LLAMA_API_KEY"]
        assert secret not in " ".join(runtime.launches[0]) and secret not in " ".join(runtime.real_argv)
        assert "receipt body" not in " ".join(runtime.real_argv)
        assert result.model_sha256 == load_pin(pin_path).model_sha256 and result.evidence["completion_tokens"] == 3
        records = list((root / "runtime" / "owners").glob("*.json"))
        assert len(records) == 1
        record = json.loads(records[0].read_text("utf-8"))
        child = runtime._proc
        assert record["child_pid"] == child.pid and record["app_pid"] == os.getpid()
        assert record["child_creation_time"] == runtime_win.creation_time(child.pid)
        assert record["app_creation_time"] == runtime_win.creation_time(os.getpid())
        assert secret not in records[0].read_text("utf-8")
        assert secret not in json.dumps(runtime.status())
        assert runtime_win.owned_listener(record["port"], child.pid)
        pid = child.pid
    finally:
        runtime.close()
    assert wait_gone(pid) and not list((root / "runtime" / "owners").glob("*.json"))
    assert runtime.status()["state"] == "closed"


@windows_only
def test_first_start_full_hash_is_once_per_instance_and_independent_of_marker(assets, monkeypatch):
    pin_path, root, script = assets
    hashed = []
    real = runtime_module.file_sha256
    monkeypatch.setattr(runtime_module, "file_sha256", lambda p: hashed.append(Path(p).name) or real(p))
    runtime = FakeChildRuntime(pin_path, root, script=script)
    try:
        runtime.generate(REQUEST)
        runtime._stop_child()
        runtime.generate(REQUEST)
    finally:
        runtime.close()
    assert sorted(hashed) == sorted(["llama-server.exe", "ggml-base.dll", load_pin(pin_path).model_sha256 + ".gguf"])


@windows_only
def test_corrupted_model_with_fresh_marker_is_refused_before_any_spawn(assets):
    pin_path, root, script = assets
    paths = runtime_paths(load_pin(pin_path), root)
    data = bytearray(paths.model.read_bytes()); data[-1] ^= 1
    stamp = paths.model.stat()
    paths.model.write_bytes(bytes(data))
    os.utime(paths.model, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    runtime = FakeChildRuntime(pin_path, root, script=script)
    assert runtime.status()["model_ready"]  # the marker only feeds readiness display
    with pytest.raises(RuntimeFailure) as caught:
        runtime.generate(REQUEST)
    runtime.close()
    assert caught.value.code == "model_integrity" and runtime.launches == []


@windows_only
def test_job_object_kills_child_when_the_owning_app_is_killed(assets, tmp_path):
    pin_path, root, script = assets
    helper = tmp_path / "owner_app.py"
    helper.write_text(
        "import sys,time\n"
        f"sys.path[:0]=[{str(Path(__file__).resolve().parents[1])!r},{str(Path(__file__).resolve().parent)!r}]\n"
        "from runtime_fakes import FakeChildRuntime\n"
        "from receipt_ledger.runtime import GenerateRequest\n"
        f"r=FakeChildRuntime({str(pin_path)!r},{str(root)!r},script={str(script)!r})\n"
        "r.generate(GenerateRequest('s','u',{}))\n"
        "print(r._proc.pid,flush=True)\n"
        "time.sleep(120)\n", "utf-8")
    app = subprocess.Popen([sys.executable, str(helper)], stdout=subprocess.PIPE, text=True)
    try:
        child = int(app.stdout.readline())
        assert alive(child)
    finally:
        app.kill()
        app.wait(10)
    assert wait_gone(child), "kill-on-close job must end the owned child"


@windows_only
def test_request_timeout_kills_child_and_next_request_restarts_with_new_secret(assets):
    pin_path, root, script = assets
    runtime = FakeChildRuntime(pin_path, root, script=script, mode="sleep")
    try:
        with pytest.raises(RuntimeFailure) as caught:
            runtime.generate(GenerateRequest("s", "u", {}, timeout_s=1.0))
        assert caught.value.code == "request_timeout" and runtime._proc is None
        first_pid_gone = len(runtime.launches) == 1
        runtime.mode = "ok"
        echoed = json.loads(runtime.generate(REQUEST).content)
        assert first_pid_gone and len(runtime.launches) == 2
        assert runtime.envs[0]["LLAMA_API_KEY"] != runtime.envs[1]["LLAMA_API_KEY"]
        assert echoed["key_sha"]
        assert runtime.status()["restarts"] == 1
    finally:
        runtime.close()


@windows_only
def test_unexpected_exit_is_reported_then_restarted_and_crash_loop_is_bounded(assets):
    pin_path, root, script = assets
    runtime = FakeChildRuntime(pin_path, root, script=script, mode="exit-after-one", max_restarts=1)
    try:
        runtime.generate(REQUEST)
        time.sleep(0.5)
        runtime.mode = "exit-now"
        with pytest.raises(RuntimeFailure) as first:
            runtime.generate(REQUEST)
        assert first.value.code == "start_failed"
        with pytest.raises(RuntimeFailure) as second:
            runtime.generate(REQUEST)
        assert second.value.code == "crash_loop"
        assert runtime.status()["state"] == "failed" and runtime.status()["model_ready"] is False
        assert runtime.status()["reason_code"] == "crash_loop"
        launches = len(runtime.launches)
        with pytest.raises(RuntimeFailure):
            runtime.generate(REQUEST)
        assert len(runtime.launches) == launches
    finally:
        runtime.close()


@windows_only
def test_foreign_listener_is_never_contacted_and_another_port_is_used(assets):
    pin_path, root, script = assets
    foreign = socket.socket(); foreign.bind(("127.0.0.1", 0)); foreign.listen(); foreign.setblocking(False)
    taken = foreign.getsockname()[1]
    runtime = FakeChildRuntime(pin_path, root, script=script, ports=[taken])
    try:
        runtime.generate(REQUEST)
        assert runtime._port != taken and len(runtime.launches) == 2
        with pytest.raises(BlockingIOError):
            foreign.accept()
    finally:
        runtime.close()
        foreign.close()


@windows_only
def test_shared_port_hijack_is_refused_by_owner_check(assets):
    pin_path, root, script = assets
    foreign = socket.socket(); foreign.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    foreign.bind(("127.0.0.1", 0)); foreign.listen(); foreign.setblocking(False)
    taken = foreign.getsockname()[1]
    runtime = FakeChildRuntime(pin_path, root, script=script, mode="reuse", ports=[taken], start_timeout_s=3.0)
    try:
        with pytest.raises(RuntimeFailure) as caught:
            runtime.generate(REQUEST)
        assert caught.value.code == "load_timeout"
        with pytest.raises(BlockingIOError):
            foreign.accept()
    finally:
        runtime.close()
        foreign.close()


@windows_only
def test_close_aborts_in_flight_request_within_bound(assets):
    pin_path, root, script = assets
    runtime = FakeChildRuntime(pin_path, root, script=script, mode="sleep")
    outcome = {}
    def call():
        try:
            runtime.generate(REQUEST)
        except RuntimeFailure as exc:
            outcome["code"] = exc.code
    worker = threading.Thread(target=call); worker.start()
    deadline = time.monotonic() + 15
    while runtime._state != "busy" and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = runtime._proc.pid
    started = time.monotonic()
    runtime.close()
    worker.join(20)
    assert time.monotonic() - started < 16 and not worker.is_alive()
    assert outcome["code"] == "cancelled" and wait_gone(pid)
    with pytest.raises(RuntimeFailure) as caught:
        runtime.generate(REQUEST)
    assert caught.value.code == "cancelled"


@windows_only
def test_stale_owner_records_are_forgotten_without_signalling_processes(assets):
    pin_path, root, script = assets
    owners = root / "runtime" / "owners"; owners.mkdir(parents=True)
    bystander = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    try:
        (owners / "stale.json").write_text(json.dumps({"app_pid": 4, "app_creation_time": 1, "child_pid": bystander.pid,
                                                       "runtime_instance": "0123456789abcdef"}), "utf-8")
        stale_temp = root / "runtime" / "tmp" / "0123456789abcdef"
        stale_temp.mkdir(parents=True)
        runtime = FakeChildRuntime(pin_path, root, script=script)
        runtime.generate(REQUEST)
        runtime.close()
        assert not (owners / "stale.json").exists() and not stale_temp.exists()
        assert bystander.poll() is None
    finally:
        bystander.kill(); bystander.wait(10)


def _owner_fixture(assets):
    pin_path, root, _ = assets
    paths = runtime_paths(load_pin(pin_path), root)
    paths.owners_dir.mkdir(parents=True)
    return pin_path, root, paths


@windows_only
def test_malformed_owner_record_first_does_not_crash_or_delete_anything_else(assets):
    pin_path, root, paths = _owner_fixture(assets)
    bad = paths.owners_dir / "01-corrupt.json"
    bad.write_text("{invalid", encoding="utf-8")
    unrelated = paths.tmp_dir / ("e" * 16)
    unrelated.mkdir(parents=True)
    runtime = LlamaRuntime(pin_path, root, idle_stop_s=None)
    runtime._prune_records(paths)
    runtime.close()
    assert not bad.exists() and unrelated.is_dir()


@windows_only
def test_live_record_then_malformed_record_keeps_live_instance_temp(assets, monkeypatch):
    pin_path, root, paths = _owner_fixture(assets)
    live_instance = "f" * 16
    live_temp = paths.tmp_dir / live_instance
    live_temp.mkdir(parents=True)
    marker = live_temp / "marker.txt"
    marker.write_text("live", encoding="utf-8")
    good = paths.owners_dir / "00-live.json"
    good.write_text(json.dumps({"app_pid": os.getpid(), "app_creation_time": runtime_win.creation_time(os.getpid()),
                                "runtime_instance": live_instance}), encoding="utf-8")
    bad = paths.owners_dir / "01-corrupt.json"
    bad.write_text("{invalid", encoding="utf-8")
    real_glob = Path.glob
    monkeypatch.setattr(Path, "glob", lambda self, pattern: iter([good, bad]) if self == paths.owners_dir and pattern == "*.json" else real_glob(self, pattern))
    runtime = LlamaRuntime(pin_path, root, idle_stop_s=None)
    runtime._prune_records(paths)
    monkeypatch.setattr(Path, "glob", real_glob)
    runtime.close()
    assert good.exists() and marker.exists() and not bad.exists()


@windows_only
def test_idle_stop_ends_child_and_next_request_restarts(assets):
    pin_path, root, script = assets
    runtime = FakeChildRuntime(pin_path, root, script=script, idle_stop_s=0.5)
    try:
        runtime.generate(REQUEST)
        pid = runtime._proc.pid
        assert wait_gone(pid, 10) and runtime.status()["state"] == "stopped"
        runtime.generate(REQUEST)
        assert len(runtime.launches) == 2
    finally:
        runtime.close()
