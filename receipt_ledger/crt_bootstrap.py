"""Load Ryoshu's verified Microsoft C++ runtime into THIS process before any native extension is imported.

Standard library only. Call activate() before uvicorn, onnxruntime, PIL, NumPy, PyPDFium or SQLCipher are
imported. It never downloads, never starts a child process or model, never creates directories, never records
consent and never accepts any agreement: it only verifies files that setup already placed and loads them by
full path, so later extensions resolve msvcp140/vcruntime140 to these exact copies by module name.

A venv's python.exe may be a redirector whose directory is venv\\Scripts; placing files in sys.base_prefix
alone does not prove where they load. activate() therefore preloads by full path and reports the module paths
Windows actually mapped.
"""
import ctypes
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from . import runtime_pin

APP_CRT = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll")  # load order
DEFAULT_ROOT = Path.home() / "StudioYebisu" / "ReceiptLedger"
DEFAULT_PIN = Path(__file__).resolve().parent / "runtime_pin.json"
_HANDLES = []   # os.add_dll_directory cookies and preloaded modules stay alive for the process lifetime
_ACTIVE = None


class CrtActivationError(RuntimeError):
    def __init__(self, code, report):
        super().__init__(code)
        self.code = code
        self.report = report


def default_root():
    return Path(os.path.abspath(os.environ.get("RECEIPT_LEDGER_RUNTIME_ROOT") or DEFAULT_ROOT))


def _sha256(path):
    try:
        return runtime_pin.file_sha256(path)
    except OSError:
        return None


def _crt_pin(pin_path):
    try:
        raw = json.loads(Path(pin_path).read_bytes().decode("utf-8"))
        crt = raw.get("crt")
        if crt is None:
            return None
        files = {f["name"].lower(): {"bytes": int(f["bytes"]), "sha256": str(f["sha256"]).lower()} for f in crt["files"]}
        version = str(crt["version"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise CrtActivationError("pin_invalid", {"pin": str(pin_path)}) from None
    if set(files) != set(APP_CRT) or not version.replace(".", "").isdigit():
        raise CrtActivationError("pin_invalid", {"pin": str(pin_path)})
    return {"version": version, "files": files}


def _inside(path, parent):
    path, parent = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(parent))
    try:
        return os.path.commonpath([path, parent]) == parent and path != parent
    except ValueError:
        return False


def _os_dir():
    return os.path.normcase(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), ""))


# Visual C++ redistributable runtime names: never "Windows" even when found in System32 (that copy comes from a
# machine-wide VC_redist install, i.e. exactly the accidental developer dependency the closure must expose).
# Wheels repaired by delvewheel vendor name-mangled copies (e.g. numpy.libs\msvcp140-<hash>.dll): same class.
_REDIST = re.compile(r"^(msvcp\d+(_[0-9a-z_]+)?|vcruntime\d+(_\d+)?|concrt\d+|vccorlib\d+|vcomp\d+|vcamp\d+|mfc\d+[a-z]*)"
                     r"(-[0-9a-f]{8,64})?\.dll$")


def classify(path):
    """'windows_component' only for a non-redistributable module under %SystemRoot%; 'redistributable_crt' for
    VC++ runtime names wherever they load; everything else 'non_os'."""
    name = os.path.basename(path).lower()
    if _REDIST.match(name):
        return "redistributable_crt"
    return "windows_component" if os.path.normcase(path).startswith(_os_dir()) else "non_os"


def loaded_modules(*, hash_non_os=True):
    """Every module mapped in this process: path, class (see classify) and SHA-256 (all modules except Windows
    components, which are hashed too unless hash_non_os is False)."""
    if os.name != "nt":
        return []
    k32, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.EnumProcessModules.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    k32.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
    proc, need = k32.GetCurrentProcess(), ctypes.c_ulong()
    count = 1024
    while True:
        mods = (ctypes.c_void_p * count)()
        if not psapi.EnumProcessModules(proc, mods, ctypes.sizeof(mods), ctypes.byref(need)):
            raise OSError(ctypes.get_last_error(), "EnumProcessModules")
        if need.value <= ctypes.sizeof(mods):
            break
        count = need.value // ctypes.sizeof(ctypes.c_void_p) + 16
    out = []
    for h in mods[:need.value // ctypes.sizeof(ctypes.c_void_p)]:
        buf = ctypes.create_unicode_buffer(32768)
        if not k32.GetModuleFileNameW(h, buf, len(buf)):
            continue
        path = buf.value
        cls = classify(path)
        out.append({"name": os.path.basename(path).lower(), "path": path, "class": cls,
                    "sha256": _sha256(path) if hash_non_os or cls != "windows_component" else None})
    return out


def _loaded_path(name):
    if os.name != "nt":
        return None
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetModuleHandleW.restype = ctypes.c_void_p
    k32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    k32.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong]
    h = k32.GetModuleHandleW(name)
    if not h:
        return None
    buf = ctypes.create_unicode_buffer(32768)
    return buf.value if k32.GetModuleFileNameW(h, buf, len(buf)) else None


def integrity(root=None, pin_path=None, python_base=None):
    """Read-only check: pinned CRT in <root>\\crt\\<version>\\ and in the app-managed Python base. No loading."""
    root = runtime_pin.absolute(root or default_root())
    pin_path = Path(pin_path or DEFAULT_PIN)
    base = runtime_pin.absolute(python_base or sys.base_prefix)
    crt = _crt_pin(pin_path)
    report = {"schema": 1, "root": str(root), "pin": str(pin_path), "python_base": str(base),
              "python_executable": sys.executable, "version": crt and crt["version"], "files": {}}
    if crt is None:
        report["state"] = "unmanaged"
        return report
    crt_dir = root / "crt" / crt["version"]
    report["crt_dir"] = str(crt_dir)
    if not _inside(base, root / "python"):
        report["state"] = "python_not_owned"   # never verify or use an interpreter outside <root>\python\
        return report
    if runtime_pin.linked_ancestry(base) or runtime_pin.linked_ancestry(crt_dir):
        report["state"] = "link_refused"
        return report
    ok = True
    for n, want in crt["files"].items():
        got = {"python_base": _sha256(base / n), "crt_dir": _sha256(crt_dir / n)}
        report["files"][n] = {"expected": want["sha256"], **got}
        ok = ok and got["python_base"] == want["sha256"] and got["crt_dir"] == want["sha256"]
    report["state"] = "verified" if ok else "not_ready"
    return report


def activate(root=None, pin_path=None, *, strict=False, python_base=None):
    """Verify and preload the pinned CRT. Returns a report; with strict=True raises CrtActivationError unless
    every pinned DLL is mapped from the app-managed Python base (or the verified crt dir) and nothing foreign
    was loaded under those names beforehand. Without strict, an unprepared machine is reported, not refused,
    so manual ledger/CSV keep working. Idempotent within a process."""
    global _ACTIVE
    if _ACTIVE is not None:
        return _ACTIVE
    report = integrity(root, pin_path, python_base)
    allowed = [report["python_base"]] + ([report["crt_dir"]] if report.get("crt_dir") else [])
    before = {n: _loaded_path(n) for n in APP_CRT}
    foreign = {n: p for n, p in before.items() if p and not any(_inside(p, d) for d in allowed)}
    report["preloaded_before"] = before
    report["foreign_before"] = foreign
    if report["state"] == "verified" and not foreign:
        if os.name == "nt":
            _HANDLES.append(os.add_dll_directory(report["python_base"]))
            for n in APP_CRT:
                _HANDLES.append(ctypes.WinDLL(os.path.join(report["python_base"], n)))
        report["state"] = "active"
    elif foreign and report["state"] == "verified":
        report["state"] = "foreign_crt_loaded"
    mapped = {n: _loaded_path(n) for n in APP_CRT}
    report["loaded"] = {n: {"path": p, "sha256": p and _sha256(p)} for n, p in mapped.items()}
    if strict and (report["state"] != "active" or any(
            v["sha256"] != report["files"][n]["expected"] for n, v in report["loaded"].items())):
        raise CrtActivationError(report["state"] if report["state"] != "active" else "loaded_mismatch", report)
    if report["state"] == "active":
        _ACTIVE = report
    return report
