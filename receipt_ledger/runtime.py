"""Owned llama-server child for local purchase-meaning suggestions.

Each app creates its own LlamaRuntime; there is no shared service and nothing
connects to a listener this object did not start. status(), models() and
provision_check() only read files. generate() is the single entry that may
start the native child, and the first start per instance SHA-256 verifies
every pinned runtime file and the model, whatever the readiness marker says.

Boundary: privacy.install() audits sockets of this Python process only. The
native child is constrained by its fixed argv, an allow-listed environment,
the pinned binary and the listener-owner check below, not by the audit hook.
"""
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import config, runtime_win
from .runtime_pin import (PinError, absolute, file_sha256, linked, linked_ancestry, load_pin, ready_state, runtime_paths,
                          safe_mkdir, unlisted_binaries)

REASON_CODES = ("pin_missing", "pin_invalid", "runtime_missing", "runtime_integrity", "model_missing", "model_unverified",
                "model_integrity", "disabled", "start_failed", "exited", "load_timeout", "request_timeout", "cancelled",
                "auth_or_owner_mismatch", "server_error", "crash_loop", "length_limit", "invalid_structured_output")
ENV_ALLOW = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER")
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
PORT_ATTEMPTS = 3


class RuntimeFailure(Exception):
    """A runtime/model problem; str() is only the reason code, never receipt content."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GenerateRequest:
    system: str
    user: str
    schema: dict
    max_tokens: int = 850
    temperature: float = 0.0
    seed: int = 42
    timeout_s: float = 300.0


@dataclass(frozen=True)
class GenerateResult:
    content: str
    finish_reason: str
    model_id: str
    model_sha256: str
    runtime_version: str
    pin_sha256: str
    seconds: float
    load_seconds: float
    evidence: dict = field(default_factory=dict)


def launch_arguments(server, model, port, threads, ctx_size):
    """Fixed argv; every flag is documented in the pinned b11700 server README."""
    return [str(server), "-m", str(model), "--host", "127.0.0.1", "--port", str(int(port)),
            "--device", "none", "-ngl", "0", "-t", str(int(threads)), "-c", str(int(ctx_size)), "-np", "1",
            "--offline", "--no-webui", "--no-slots", "--jinja", "--reasoning", "off", "--log-disable"]


def child_environment(parent, secret, runtime_dir, temp_dir):
    """Built from an allow-list; inherited LLAMA_*, HF_*, proxy and RPC settings never reach the child."""
    upper = {str(k).upper(): v for k, v in parent.items()}
    env = {name: upper[name] for name in ENV_ALLOW if name in upper}
    system32 = Path(env.get("SYSTEMROOT", r"C:\Windows")) / "System32"
    env["PATH"] = str(runtime_dir) + os.pathsep + str(system32)
    env["TEMP"] = env["TMP"] = str(temp_dir)
    env["LLAMA_API_KEY"] = secret
    return env


def request_body(request):
    return {"messages": [{"role": "system", "content": request.system}, {"role": "user", "content": request.user}],
            # Native b11700 smoke: {"type":"json_schema","schema":...} is accepted but NOT enforced;
            # the OpenAI nested form below is enforced (harness-backend/qa/native-20261027b probe-schema.json).
            "response_format": {"type": "json_schema", "json_schema": {"name": "receipt", "schema": request.schema, "strict": True}},
            "chat_template_kwargs": {"enable_thinking": False},
            "temperature": request.temperature, "seed": request.seed, "max_tokens": request.max_tokens, "stream": False}


def parse_response(data):
    """Return (content, finish_reason, evidence); reasoning text is measured, never kept."""
    if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]:
        raise ValueError("unexpected response shape")
    choice = data["choices"][0]
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("unexpected response shape")
    content, finish = message["content"], choice.get("finish_reason")
    reasoning = message.get("reasoning_content")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    evidence = {"finish_reason": finish if isinstance(finish, str) else "",
                "content_chars": len(content),
                "reasoning_content_chars": len(reasoning) if isinstance(reasoning, str) else -1,
                "content_has_think_tag": "<think>" in content or "</think>" in content,
                "prompt_tokens": usage.get("prompt_tokens") if type(usage.get("prompt_tokens")) is int else None,
                "completion_tokens": usage.get("completion_tokens") if type(usage.get("completion_tokens")) is int else None}
    return content, evidence["finish_reason"], evidence


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class DisabledRuntime:
    """Used when no runtime is injected (tests, manual-only apps); never starts anything."""

    def provision_check(self):
        return {"runtime_ok": False, "model_ok": False, "reason_code": "disabled", "runtime_version": "",
                "model_sha256": "", "model_id": "", "pin_sha256": ""}

    def status(self):
        return {"state": "disabled", "runtime_ready": False, "model_ready": False, "reason_code": "disabled",
                "runtime_version": "", "model_id": "", "model_sha256": "", "restarts": 0}

    def models(self):
        raise RuntimeFailure("disabled")

    def generate(self, request):
        raise RuntimeFailure("disabled")

    def close(self):
        pass


class LlamaRuntime:
    def __init__(self, pin_path=None, root=None, *, threads=config.RUNTIME_THREADS, ctx_size=config.RUNTIME_CTX,
                 start_timeout_s=180.0, stop_timeout_s=10.0, idle_stop_s=300.0, max_restarts=3, restart_window_s=600.0):
        self.pin_path = Path(pin_path or config.RUNTIME_PIN)
        # Never resolve: a junction/symlink root must remain visible to the link checks.
        self.root = absolute(root or config.RUNTIME_ROOT)
        self.threads, self.ctx_size = threads, ctx_size
        self.start_timeout_s, self.stop_timeout_s, self.idle_stop_s = start_timeout_s, stop_timeout_s, idle_stop_s
        self.max_restarts, self.restart_window_s = max_restarts, restart_window_s
        self.instance = secrets.token_hex(8)
        self._lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._closed = threading.Event()
        self._hash_cache = {}
        self._verified = None
        self._verified_model_sha = ""
        self._pin = self._paths = None
        self._proc = self._job = self._port = self._secret = self._record = None
        self._state, self._failure = "stopped", ""
        self._started_once = False
        self._restarts = deque()
        self._restart_total = 0
        self._idle_timer = None

    # Overridable seams (tests substitute a harmless owned child).
    def _command(self, paths, pin, port):
        return launch_arguments(paths.server, paths.model, port, self.threads, self.ctx_size)

    def _spawn(self, argv, env, cwd):
        return subprocess.Popen(argv, env=env, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)

    def _new_job(self):
        return runtime_win.Job() if runtime_win.WINDOWS else None

    def _owned(self, port, pid):
        return runtime_win.owned_listener(port, pid)

    def _choose_port(self):
        return free_port()

    # Pure readiness: files only.
    def provision_check(self):
        return ready_state(self.pin_path, self.root, hash_cache=self._hash_cache)

    def status(self):
        provision = self.provision_check()
        with self._lock:
            state, failure, restarts = self._state, self._failure, self._restart_total
        blocked = state in {"failed", "closed"}
        return {"state": state, "runtime_ready": provision["runtime_ok"] and not blocked,
                "model_ready": provision["runtime_ok"] and provision["model_ok"] and not blocked,
                "reason_code": failure if blocked else provision["reason_code"],
                "runtime_version": provision["runtime_version"], "model_id": provision["model_id"],
                "model_sha256": provision["model_sha256"], "restarts": restarts}

    def models(self):
        provision = self.provision_check()
        if not (provision["runtime_ok"] and provision["model_ok"]):
            raise RuntimeFailure(provision["reason_code"])
        pin = load_pin(self.pin_path)
        return [{"id": pin.model_id, "sha256": pin.model_sha256, "bytes": pin.model_bytes, "runtime_version": pin.runtime_tag}]

    # Inference.
    def generate(self, request):
        with self._request_lock:
            if self._closed.is_set():
                raise RuntimeFailure("cancelled")
            with self._lock:
                if self._state == "failed":
                    raise RuntimeFailure(self._failure)
            self._cancel_idle()
            try:
                load_seconds = self._ensure_started()
                with self._lock:
                    proc, port, secret, pin = self._proc, self._port, self._secret, self._pin
                    if self._closed.is_set():
                        proc = None
                    else:
                        self._state = "busy"
                if proc is None:
                    raise RuntimeFailure("cancelled")
                if not self._owned(port, proc.pid):
                    self._stop_child()
                    raise RuntimeFailure("auth_or_owner_mismatch")
                started = time.perf_counter()
                try:
                    with httpx.Client(trust_env=False, follow_redirects=False,
                                      timeout=httpx.Timeout(request.timeout_s, connect=5)) as client:
                        response = client.post(f"http://127.0.0.1:{port}/v1/chat/completions", json=request_body(request),
                                               headers={"Authorization": "Bearer " + secret})
                except httpx.TimeoutException:
                    self._stop_child()
                    raise RuntimeFailure("cancelled" if self._closed.is_set() else "request_timeout") from None
                except httpx.TransportError:
                    self._stop_child()
                    raise RuntimeFailure("cancelled" if self._closed.is_set() else "exited") from None
                if response.status_code == 401:
                    self._stop_child()
                    raise RuntimeFailure("auth_or_owner_mismatch")
                if response.status_code != 200:
                    raise RuntimeFailure("server_error")
                try:
                    content, finish, evidence = parse_response(response.json())
                except ValueError:
                    raise RuntimeFailure("invalid_structured_output") from None
                return GenerateResult(content=content, finish_reason=finish, model_id=pin.model_id,
                                      model_sha256=self._verified_model_sha, runtime_version=pin.runtime_tag,
                                      pin_sha256=pin.sha256, seconds=round(time.perf_counter() - started, 3),
                                      load_seconds=round(load_seconds, 3), evidence=evidence)
            finally:
                with self._lock:
                    if self._closed.is_set():
                        self._state, self._failure = "closed", "cancelled"
                    elif self._state in {"busy", "starting"}:
                        self._state = "ready" if self._proc is not None and self._proc.poll() is None else "stopped"
                self._arm_idle()

    def close(self):
        self._closed.set()
        self._cancel_idle()
        self._stop_child()
        with self._lock:
            self._state, self._failure = "closed", "cancelled"
            paths = self._paths
        temp = paths.tmp_dir / self.instance if paths is not None else None
        if temp is not None and temp.is_dir() and not linked_ancestry(paths.root) and not linked(temp, paths.root):
            shutil.rmtree(temp, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # Lifecycle internals.
    def _ensure_started(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            return 0.0
        if proc is not None:
            self._stop_child()
        if self._started_once:
            now = time.monotonic()
            self._restarts.append(now)
            while self._restarts and now - self._restarts[0] > self.restart_window_s:
                self._restarts.popleft()
            self._restart_total += 1
            if len(self._restarts) > self.max_restarts:
                with self._lock:
                    self._state, self._failure = "failed", "crash_loop"
                raise RuntimeFailure("crash_loop")
        self._started_once = True
        began = time.monotonic()
        try:
            pin = load_pin(self.pin_path)
        except PinError as exc:
            raise RuntimeFailure(exc.code) from None
        paths = runtime_paths(pin, self.root)
        if linked_ancestry(paths.root):
            raise RuntimeFailure("runtime_integrity")
        # Hashing runs without the lock so close() stays bounded; closed is re-checked after it.
        self._verify_assets(pin, paths)
        with self._lock:
            if self._closed.is_set():
                raise RuntimeFailure("cancelled")
            self._pin, self._paths, self._state = pin, paths, "starting"
        self._prune_records(paths)
        temp = paths.tmp_dir / self.instance
        try:
            safe_mkdir(temp, paths.root)
        except OSError:
            raise RuntimeFailure("runtime_integrity") from None
        if self._closed.is_set():
            # close() may have finished its cleanup before this directory existed.
            if not linked(temp, paths.root):
                shutil.rmtree(temp, ignore_errors=True)
            raise RuntimeFailure("cancelled")
        for _ in range(PORT_ATTEMPTS):
            if self._closed.is_set():
                raise RuntimeFailure("cancelled")
            port, secret = self._choose_port(), secrets.token_urlsafe(32)
            env = child_environment(os.environ, secret, paths.runtime_dir, temp)
            try:
                job = self._new_job()
            except OSError:
                raise RuntimeFailure("start_failed") from None
            try:
                proc = self._spawn(self._command(paths, pin, port), env, paths.runtime_dir)
            except OSError:
                if job is not None:
                    job.close()
                raise RuntimeFailure("start_failed") from None
            with self._lock:
                self._proc, self._job, self._port, self._secret = proc, job, port, secret
            if job is not None:
                try:
                    job.assign(proc)
                except OSError:
                    self._stop_child()
                    raise RuntimeFailure("start_failed") from None
            if self._closed.is_set():
                self._stop_child()
                raise RuntimeFailure("cancelled")
            outcome = self._wait_ready(proc, port, began)
            if outcome == "ready":
                try:
                    self._write_record(paths, pin, proc, port)
                except (OSError, RuntimeFailure):
                    # Without an ownership record the child must not outlive this call.
                    self._stop_child()
                    raise RuntimeFailure("runtime_integrity") from None
                with self._lock:
                    closed = self._closed.is_set()
                    if not closed:
                        self._state = "ready"
                if closed:
                    self._stop_child()  # removes the just-written record too
                    raise RuntimeFailure("cancelled")
                return time.monotonic() - began
            self._stop_child()
            if outcome != "exited":
                raise RuntimeFailure(outcome)
        raise RuntimeFailure("start_failed")

    def _wait_ready(self, proc, port, began):
        deadline = began + self.start_timeout_s
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=httpx.Timeout(2.0)) as client:
            while True:
                if self._closed.is_set():
                    return "cancelled"
                if proc.poll() is not None:
                    return "exited"
                if time.monotonic() > deadline:
                    return "load_timeout"
                # The public /health probe carries no secret or receipt; it is still
                # only sent once our own child PID is the sole loopback listener.
                if self._owned(port, proc.pid):
                    try:
                        if client.get(f"http://127.0.0.1:{port}/health").status_code == 200:
                            return "ready" if self._owned(port, proc.pid) else "auth_or_owner_mismatch"
                    except httpx.TransportError:
                        pass
                self._closed.wait(0.2)

    def _verify_assets(self, pin, paths):
        stamps = []
        for member, path in paths.members:
            if linked(path, paths.root):
                raise RuntimeFailure("runtime_integrity")
            if not path.is_file():
                raise RuntimeFailure("runtime_missing")
            info = path.stat()
            stamps.append((str(path), info.st_size, info.st_mtime_ns))
        if paths.model.exists() and linked(paths.model, paths.root):
            raise RuntimeFailure("model_integrity")
        if not paths.model.is_file():
            raise RuntimeFailure("model_missing")
        info = paths.model.stat()
        stamps.append((str(paths.model), info.st_size, info.st_mtime_ns))
        # Checked on every spawn, before the once-per-instance hash shortcut.
        if unlisted_binaries(paths):
            raise RuntimeFailure("runtime_integrity")
        stamp = (pin.sha256, tuple(stamps))
        if self._verified == stamp:
            return
        for member, path in paths.members:
            if path.stat().st_size != member.bytes or file_sha256(path) != member.sha256:
                raise RuntimeFailure("runtime_integrity")
        if info.st_size != pin.model_bytes:
            raise RuntimeFailure("model_integrity")
        actual = file_sha256(paths.model)
        if actual != pin.model_sha256:
            raise RuntimeFailure("model_integrity")
        self._verified_model_sha = actual
        self._verified = stamp

    def _write_record(self, paths, pin, proc, port):
        owners = safe_mkdir(paths.owners_dir, paths.root)
        app_pid = os.getpid()
        app_created = runtime_win.creation_time(app_pid)
        server = next(m for m, _ in paths.members if m.name == pin.server_exe)
        record = {"schema": 1, "app_pid": app_pid, "app_creation_time": app_created, "runtime_instance": self.instance,
                  "child_pid": proc.pid, "child_creation_time": runtime_win.creation_time(proc.pid),
                  "exe_path": str(absolute(paths.server)), "exe_sha256": server.sha256, "port": port,
                  "written_at": datetime.now(timezone.utc).isoformat()}
        target = owners / f"{app_pid}-{app_created}-{self.instance}.json"
        temporary = target.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(record), encoding="utf-8")
            os.replace(temporary, target)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
        with self._lock:
            self._record = target

    def _prune_records(self, paths):
        """Forget records of app processes that no longer exist; never signals any process."""
        if not runtime_win.WINDOWS or not paths.owners_dir.is_dir() or linked(paths.owners_dir, paths.root):
            return
        decoded = []
        for item in paths.owners_dir.glob("*.json"):
            # Fresh per-record state: a malformed file never borrows a previous record.
            instance, alive = None, False
            try:
                record = json.loads(item.read_text(encoding="utf-8"))
                if isinstance(record, dict):
                    pid = int(record["app_pid"])
                    alive = runtime_win.running(pid) and runtime_win.creation_time(pid) == record["app_creation_time"]
                    candidate = record.get("runtime_instance")
                    if isinstance(candidate, str) and len(candidate) == 16 and all(c in "0123456789abcdef" for c in candidate):
                        instance = candidate
            except (OSError, ValueError, KeyError, TypeError):
                instance, alive = None, False
            decoded.append((item, instance, alive))
        protected = {self.instance} | {instance for _, instance, alive in decoded if alive and instance}
        for item, instance, alive in decoded:
            if alive:
                continue
            if instance is not None and instance not in protected:
                temp = paths.tmp_dir / instance
                if temp.is_dir() and not linked(temp, paths.root):
                    shutil.rmtree(temp, ignore_errors=True)
            item.unlink(missing_ok=True)

    def _stop_child(self):
        with self._lock:
            proc, job, record = self._proc, self._job, self._record
            self._proc = self._job = self._port = self._secret = self._record = None
            if self._state not in {"failed", "closed"}:
                self._state = "stopped"
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(self.stop_timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    pass
            except OSError:
                pass
        if job is not None:
            job.close()
        if record is not None:
            record.unlink(missing_ok=True)

    def _arm_idle(self):
        if self.idle_stop_s is None or self._closed.is_set():
            return
        timer = threading.Timer(self.idle_stop_s, self._idle_stop)
        timer.daemon = True
        with self._lock:
            previous, self._idle_timer = self._idle_timer, timer
        if previous is not None:
            previous.cancel()
        timer.start()

    def _cancel_idle(self):
        with self._lock:
            timer, self._idle_timer = self._idle_timer, None
        if timer is not None:
            timer.cancel()

    def _idle_stop(self):
        if not self._request_lock.acquire(blocking=False):
            return
        try:
            with self._lock:
                current = self._idle_timer is threading.current_thread()
            if current:
                self._stop_child()
        finally:
            self._request_lock.release()
