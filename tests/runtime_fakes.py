"""Synthetic runtime fixtures. Values here are technical test pins only, never product pins."""
import hashlib
import json
import os
from pathlib import Path
import sys
import threading

from receipt_ledger.runtime import GenerateResult, LlamaRuntime, RuntimeFailure

SYNTHETIC_COMMIT = "a" * 40
SYNTHETIC_REVISION = "b" * 40
SYNTHETIC_TEMPLATE = "c" * 64

FAKE_SERVER = r'''
import hashlib, json, os, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
port, mode = int(sys.argv[1]), sys.argv[2]
key = os.environ.get("LLAMA_API_KEY", "")
if mode == "exit-now":
    sys.exit(3)
served = {"n": 0}
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def reply(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/health":
            self.reply(200, {"status": "ok"})
        else:
            self.reply(404, {})
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.headers.get("Authorization") != "Bearer " + key:
            return self.reply(401, {"error": "unauthorized"})
        if mode == "sleep":
            time.sleep(60)
        content = json.dumps({"argv": sys.argv, "env_keys": sorted(os.environ), "body": body,
                              "key_sha": hashlib.sha256(key.encode()).hexdigest()})
        self.reply(200, {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 7, "completion_tokens": 3}})
        served["n"] += 1
        if mode == "exit-after-one":
            self.wfile.flush(); os._exit(0)
class Server(ThreadingHTTPServer):
    allow_reuse_address = mode == "reuse"
    daemon_threads = True
Server(("127.0.0.1", port), Handler).serve_forever()
'''


def write_fake_server(folder):
    path = Path(folder) / "fake_llama_server.py"
    path.write_text(FAKE_SERVER, encoding="utf-8")
    return path


def make_pin(tmp, *, marker=True, extra=None):
    """Create a synthetic pin and matching files under tmp/root; returns (pin_path, root)."""
    tmp = Path(tmp)
    root = tmp / "root"
    runtime_dir = root / "llama" / "synthetic-cpu"
    runtime_dir.mkdir(parents=True)
    members = []
    for name, content in [("llama-server.exe", b"MZ synthetic server"), ("ggml-base.dll", b"synthetic dll")]:
        (runtime_dir / name).write_bytes(content)
        members.append({"name": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    model_bytes = b"GGUF" + bytes(range(256)) * 8
    model_sha = hashlib.sha256(model_bytes).hexdigest()
    model = root / "models" / (model_sha + ".gguf")
    model.parent.mkdir(parents=True)
    model.write_bytes(model_bytes)
    if marker:
        info = model.stat()
        model.with_name(model.name + ".verified.json").write_text(json.dumps(
            {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "sha256": model_sha, "pin_sha": "synthetic"}), "utf-8")
    pin = {"schema": 1,
           "runtime": {"tag": "synthetic-test", "commit": SYNTHETIC_COMMIT, "dir_name": "synthetic-cpu",
                       "server_exe": "llama-server.exe", "members": members, "url": "https://invalid.example/not-used"},
           "model": {"repo": "synthetic/test-model", "revision": SYNTHETIC_REVISION, "file": "synthetic.gguf",
                     "bytes": len(model_bytes), "sha256": model_sha, "license": "test-only",
                     "chat_template_sha256": SYNTHETIC_TEMPLATE}}
    if extra:
        extra(pin)
    pin_path = tmp / "runtime_pin.json"
    pin_path.write_text(json.dumps(pin), "utf-8")
    return pin_path, root


class FakeChildRuntime(LlamaRuntime):
    """Real LlamaRuntime lifecycle with a harmless owned Python HTTP child instead of llama-server."""

    def __init__(self, pin_path, root, *, mode="ok", script=None, ports=None, **kwargs):
        kwargs.setdefault("start_timeout_s", 20.0)
        kwargs.setdefault("idle_stop_s", None)
        super().__init__(pin_path, root, **kwargs)
        self.mode, self.script = mode, script
        self.ports = list(ports or [])
        self.launches = []
        self.envs = []

    def _command(self, paths, pin, port):
        self.real_argv = super()._command(paths, pin, port)
        # A venv python.exe is a redirector that listens from a second PID; the owner
        # check would (correctly) refuse it, so the stdlib-only fake uses the base interpreter.
        return [getattr(sys, "_base_executable", sys.executable), "-I", str(self.script), str(port), self.mode]

    def _spawn(self, argv, env, cwd):
        self.launches.append(list(argv))
        self.envs.append(dict(env))
        return super()._spawn(argv, env, cwd)

    def _choose_port(self):
        return self.ports.pop(0) if self.ports else super()._choose_port()


class ScriptedRuntime:
    """In-process stand-in for app/semantic tests; never starts a process."""

    def __init__(self, contents=(), error=None, finish_reason="stop"):
        self.contents, self.error, self.finish_reason = list(contents), error, finish_reason
        self.requests, self.closed = [], 0
        self.lock = threading.Lock()

    def provision_check(self):
        return {"runtime_ok": True, "model_ok": True, "reason_code": "", "runtime_version": "scripted",
                "model_sha256": "d" * 64, "model_id": "scripted/model", "pin_sha256": "e" * 64}

    def status(self):
        return {"state": "stopped", "runtime_ready": True, "model_ready": True, "reason_code": "",
                "runtime_version": "scripted", "model_id": "scripted/model", "model_sha256": "d" * 64, "restarts": 0}

    def models(self):
        return [{"id": "scripted/model", "sha256": "d" * 64, "bytes": 1, "runtime_version": "scripted"}]

    def generate(self, request):
        with self.lock:
            self.requests.append(request)
        if self.error:
            raise RuntimeFailure(self.error)
        content = self.contents.pop(0) if self.contents else "{}"
        return GenerateResult(content=content, finish_reason=self.finish_reason, model_id="scripted/model",
                              model_sha256="d" * 64, runtime_version="scripted", pin_sha256="e" * 64,
                              seconds=0.01, load_seconds=0.0, evidence={"finish_reason": self.finish_reason})

    def close(self):
        self.closed += 1
