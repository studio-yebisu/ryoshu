import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from receipt_ledger import crt_bootstrap as cb  # noqa: E402

sha = lambda b: hashlib.sha256(b).hexdigest()
win = pytest.mark.skipif(os.name != "nt", reason="Windows loader")


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(cb, "_ACTIVE", None)


def make_root(tmp_path, files=None):
    root = tmp_path / "profile 日本語 space" / "StudioYebisu" / "ReceiptLedger"
    files = files or {n: b"MZ fake " + n.encode() for n in cb.APP_CRT}
    version = "14.51.36247.0"
    base = root / "python" / "cpython-3.12.15-windows-x86_64-none"
    crt = root / "crt" / version
    for d in (base, crt):
        d.mkdir(parents=True)
        for n, b in files.items():
            (d / n).write_bytes(b)
    pin = tmp_path / "runtime_pin.json"
    pin.write_text(json.dumps({"schema": 1, "crt": {"version": version, "files": [
        {"name": n, "bytes": len(b), "sha256": sha(b)} for n, b in files.items()]}}), "utf-8")
    return root, base, crt, pin


def test_integrity_verifies_python_base_and_crt_dir_without_writing(tmp_path):
    root, base, crt, pin = make_root(tmp_path)
    before = sorted(p for p in tmp_path.rglob("*"))
    rep = cb.integrity(root, pin, base)
    assert rep["state"] == "verified" and rep["version"] == "14.51.36247.0"
    assert all(f["python_base"] == f["expected"] == f["crt_dir"] for f in rep["files"].values())
    assert sorted(p for p in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("where", ["outside", "python home itself", "dotdot"])
def test_interpreter_outside_root_python_is_not_owned(tmp_path, where):
    root, base, crt, pin = make_root(tmp_path)
    other = {"outside": tmp_path / "shared", "python home itself": root / "python",
             "dotdot": Path(str(root / "python" / ".." / "elsewhere"))}[where]
    assert cb.integrity(root, pin, other)["state"] == "python_not_owned"
    with pytest.raises(cb.CrtActivationError) as e:
        cb.activate(root, pin, python_base=other, strict=True)
    assert e.value.code == "python_not_owned"


def test_tampered_or_missing_member_is_not_ready_and_non_strict_does_not_raise(tmp_path):
    root, base, crt, pin = make_root(tmp_path)
    (base / "msvcp140.dll").write_bytes(b"MZ other")
    (crt / "vcruntime140_1.dll").unlink()
    rep = cb.activate(root, pin, python_base=base)
    assert rep["state"] == "not_ready" and cb._ACTIVE is None
    with pytest.raises(cb.CrtActivationError) as e:
        cb.activate(root, pin, python_base=base, strict=True)
    assert e.value.code == "not_ready"


def test_pin_without_crt_section_is_unmanaged_and_invalid_pin_refused(tmp_path):
    root, base, crt, pin = make_root(tmp_path)
    pin.write_text(json.dumps({"schema": 1}), "utf-8")
    assert cb.activate(root, pin, python_base=base)["state"] == "unmanaged"
    pin.write_text(json.dumps({"crt": {"version": "x", "files": []}}), "utf-8")
    with pytest.raises(cb.CrtActivationError) as e:
        cb.integrity(root, pin, base)
    assert e.value.code == "pin_invalid"


@win
def test_strict_refuses_when_a_different_copy_is_already_loaded(tmp_path):
    # This test process already mapped its own interpreter's vcruntime140.dll; a verified copy elsewhere
    # must not be silently mixed in: strict mode refuses, non-strict reports.
    real = {n: (Path(sys.base_prefix) / n).read_bytes() for n in ("vcruntime140.dll", "vcruntime140_1.dll")}
    files = {**real, "msvcp140.dll": b"MZ fake msvcp140", "msvcp140_1.dll": b"MZ fake msvcp140_1"}
    root, base, crt, pin = make_root(tmp_path, files)
    with pytest.raises(cb.CrtActivationError) as e:
        cb.activate(root, pin, python_base=base, strict=True)
    assert e.value.code == "foreign_crt_loaded"
    assert "vcruntime140.dll" in e.value.report["foreign_before"]
    assert cb.activate(root, pin, python_base=base)["state"] == "foreign_crt_loaded"


@win
def test_loaded_modules_classifies_os_and_hashes_non_os():
    mods = cb.loaded_modules()
    assert any(m["class"] == "windows_component" and m["name"] == "kernel32.dll" for m in mods)
    exe = [m for m in mods if m["class"] == "non_os" and m["name"].startswith("python")]
    assert exe and all(len(m["sha256"]) == 64 for m in exe)
    assert all(len(m["sha256"] or "") == 64 for m in mods if m["class"] != "windows_component")


def test_system32_redistributable_crt_is_never_classified_as_windows(monkeypatch):
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    for n in ("msvcp140.dll", "MSVCP140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll", "concrt140.dll", "vcomp140.dll",
              "msvcp140_atomic_wait.dll", "mfc140u.dll"):
        assert cb.classify(r"C:\Windows\System32" + "\\" + n) == "redistributable_crt"
    assert cb.classify(r"C:\Windows\System32\ucrtbase.dll") == "windows_component"
    assert cb.classify(r"C:\Windows\System32\msvcp_win.dll") == "windows_component"
    assert cb.classify(r"D:\x\onnxruntime.dll") == "non_os"
    # delvewheel-vendored copy inside a wheel (seen in numpy.libs during the actual-5 QA)
    assert cb.classify(r"D:\venv\Lib\site-packages\numpy.libs\msvcp140-a4c2229bdc2a2a630acdc095b4d86008.dll") == "redistributable_crt"
    assert cb.classify(r"D:\venv\Lib\site-packages\numpy.libs\libscipy_openblas64_-ed4f167a5330424524f45258e7ca2c8d.dll") == "non_os"


def test_main_activates_before_any_native_import():
    tree = ast.parse((ROOT / "receipt_ledger/__main__.py").read_text("utf-8"))
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    names = {a.name for n in top for a in n.names} | {n.module for n in top if isinstance(n, ast.ImportFrom) and n.module}
    assert names <= {"sys", "config", "crt_bootstrap", "privacy", "install"}
    body = next(n for n in tree.body if isinstance(n, ast.If)).body
    src = [ast.unparse(s) for s in body]
    act = next(i for i, s in enumerate(src) if "crt_bootstrap.activate(" in s)
    first_native = next(i for i, s in enumerate(src) if s.startswith(("import ", "from ")))
    assert act < first_native and "strict" not in src[act]  # not a model gate


def test_bootstrap_import_chain_is_standard_library_only():
    code = ("import sys;b=set(sys.modules);import receipt_ledger.crt_bootstrap, receipt_ledger.config, receipt_ledger.privacy;"
            "bad=[m for m in set(sys.modules)-b if m.split('.')[0] in "
            "('uvicorn','onnxruntime','numpy','PIL','pypdfium2','sqlcipher3','cryptography','fastapi','starlette','pydantic')];"
            "print(bad);raise SystemExit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-I", "-B", "-c", f"import sys;sys.path.insert(0,{str(ROOT)!r});{code}"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
