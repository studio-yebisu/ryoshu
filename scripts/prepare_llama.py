"""First-setup preparation of the pinned llama.cpp runtime and Qwen GGUF (standard library only).

The source pin receipt_ledger/runtime_pin.json is the only authority for what is
downloaded and installed. Installed receipts/markers record what was verified and
never change the selection. Network is used only here, only over HTTPS, only to the
pinned hosts, and every byte is checked (size + SHA-256) before anything is extracted,
renamed or executed. A failure leaves the previous runtime, model and all ledger data
untouched.

Layout under <task_root> (shared contract with receipt_ledger.runtime_pin):
  llama/<runtime.dir_name>/            exactly the pinned members (incl. license texts)
  llama/<runtime.dir_name>.receipt.json  installer receipt (observed verification facts)
  models/<model.sha256>.gguf           model
  models/<model.sha256>.gguf.verified.json  marker {size, mtime_ns, sha256, pin_sha}
  models/<model.sha256>.LICENSE.txt    model license text (exact pinned bytes)
  downloads/<sha256>.part              resumable partial downloads
Result for setup.ps1: <task_root>/runtime/llama-setup-result.json {ok, reason_code}.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))
from receipt_ledger import runtime_pin  # noqa: E402  (stdlib-only module owned by the backend)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ms_vcredist  # noqa: E402  (owned: reads the official Microsoft package as data only)

CHUNK = 1024 * 1024
VC_RUNTIME = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")
# App-wide Microsoft C++ runtime closure: llama.cpp DLLs import msvcp140/vcruntime140/_1; the pinned
# onnxruntime 1.31 wheel additionally imports msvcp140_1. The managed Python ships only vcruntime140/_1.
APP_CRT = ("msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll")
PYTHON_ADDED_CRT = ("msvcp140.dll", "msvcp140_1.dll")      # never replace Python's own vcruntime files
PYTHON_OWN_CRT = ("vcruntime140.dll", "vcruntime140_1.dll")
SPACE_MARGIN = 512 * 1024 * 1024


class PrepareError(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


# --- pin -------------------------------------------------------------------------------------------
def load_install_pin(path: Path) -> tuple[runtime_pin.Pin, dict]:
    """Validate with the backend loader, then read the installer-only download keys."""
    try:
        pin = runtime_pin.load_pin(path)
    except runtime_pin.PinError as exc:
        raise PrepareError(exc.code) from None
    raw = json.loads(path.read_bytes().decode("utf-8"))
    rt, md = raw["runtime"], raw["model"]
    try:
        extra = {
            "runtime_url": _https(rt["asset_url"]), "runtime_bytes": _pos(rt["asset_bytes"]),
            "runtime_sha256": _hex64(rt["asset_sha256"]), "runtime_hosts": _hosts(rt["download_hosts"]),
            "runtime_licenses": [{"name": l["name"], "source": l["source"]} for l in rt["license_files"]],
            "model_url": _https(md["url"]), "model_hosts": _hosts(md["download_hosts"]),
            "model_license_source": md["license_file"]["source"],
            "model_license_sha256": _hex64(md["license_file"]["sha256"]),
        }
    except (KeyError, TypeError, ValueError):
        raise PrepareError("pin_invalid") from None
    members = {m.name for m in pin.members}
    for lic in extra["runtime_licenses"]:
        if lic["name"] not in members:
            raise PrepareError("pin_invalid")
    return pin, extra


def _https(url):
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or not p.netloc or p.query or p.fragment or "@" in p.netloc:
        raise ValueError(url)
    return url


def _pos(v):
    if type(v) is not int or v <= 0:
        raise ValueError(v)
    return v


def _hex64(v):
    if not isinstance(v, str) or not runtime_pin.HEX64.fullmatch(v):
        raise ValueError(v)
    return v


def _hosts(v):
    if not isinstance(v, list) or not v or not all(isinstance(h, str) and h and "/" not in h and ":" not in h for h in v):
        raise ValueError(v)
    return [h.lower() for h in v]


def host_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower()
    for a in allowed:
        if a.startswith("*."):
            if host.endswith(a[1:]) and host.count(".") >= a.count("."):
                return True
        elif host == a:
            return True
    return False


# --- filesystem safety -----------------------------------------------------------------------------
def safe_root(root) -> Path:
    """Lexical absolute root (never .resolve(): that would erase a junction/symlink) validated with every
    existing ancestor BEFORE anything is created, written, downloaded or moved aside under it."""
    root = runtime_pin.absolute(root)
    if runtime_pin.linked_ancestry(root):
        raise PrepareError("link_refused")
    return root


def refuse_links(path: Path, root: Path):
    if runtime_pin.linked(path, root):
        raise PrepareError("link_refused")


def ensure_dir(path: Path, root: Path):
    # one level at a time, refusing before any link is written through (backend runtime_pin.safe_mkdir)
    try:
        runtime_pin.safe_mkdir(path, root)
    except OSError:
        raise PrepareError("link_refused") from None
    refuse_links(path, root)


def sha256_file(path: Path) -> str:
    return runtime_pin.file_sha256(path)


# --- download --------------------------------------------------------------------------------------
class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed, hops):
        self.allowed, self.hops = allowed, hops

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlsplit(newurl)
        self.hops.append({"status": code, "host": p.netloc})  # never the path query or signature
        if p.scheme != "https" or not host_allowed(p.hostname or "", self.allowed):
            raise PrepareError("download_host_refused", p.netloc)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and req.has_header("Range"):
            new.add_header("Range", req.get_header("Range"))
        return new


def open_url(url, allowed, hops, start=0, timeout=60):
    if not host_allowed(urllib.parse.urlsplit(url).hostname or "", allowed):
        raise PrepareError("download_host_refused")
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx),
                                         _Redirects(allowed, hops))
    req = urllib.request.Request(url, headers={"User-Agent": "Ryoshu-setup"})
    if start:
        req.add_header("Range", f"bytes={start}-")
    return opener.open(req, timeout=timeout)


def download(url, allowed, expected_bytes, expected_sha, downloads: Path, root: Path, *, opener=open_url) -> Path:
    """Resumable download into downloads/<sha>.part; returns the verified file (renamed to <sha>.bin)."""
    ensure_dir(downloads, root)
    final = downloads / f"{expected_sha}.bin"
    part = downloads / f"{expected_sha}.part"
    for p in (final, part):
        refuse_links(p, root)
    if final.exists():
        if final.stat().st_size == expected_bytes and sha256_file(final) == expected_sha:
            return final
        final.unlink()
    have = part.stat().st_size if part.exists() else 0
    if have > expected_bytes:
        part.unlink()
        have = 0
    if shutil.disk_usage(downloads).free < expected_bytes - have + SPACE_MARGIN:
        raise PrepareError("disk_space")
    hops: list = []
    try:
        with opener(url, allowed, hops, have) as response:
            status = getattr(response, "status", 200)
            mode = "ab"
            if have and status != 206:
                mode, have = "wb", 0  # server ignored the range: restart from zero
            elif not have and status not in (200,):
                raise PrepareError("download_failed", f"http {status}")
            with open(part, mode) as out:
                written = have
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    written += len(block)
                    if written > expected_bytes:
                        raise PrepareError("download_integrity", "oversize")
                    out.write(block)
    except PrepareError as exc:
        if exc.code == "download_integrity":
            part.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
        raise PrepareError("download_failed", type(exc).__name__) from None
    if part.stat().st_size != expected_bytes:
        raise PrepareError("download_failed", "incomplete")  # keep .part for resume
    if sha256_file(part) != expected_sha:
        part.unlink()
        raise PrepareError("download_integrity")
    os.replace(part, final)
    return final


# --- runtime install -------------------------------------------------------------------------------
def safe_member_name(info: zipfile.ZipInfo) -> str | None:
    name = info.orig_filename
    pure = PurePosixPath(name)
    if ("\\" in name or ":" in name or "\x00" in name or pure.is_absolute() or ".." in pure.parts
            or stat.S_ISLNK(info.external_attr >> 16)):
        raise PrepareError("archive_unsafe", name)
    return None if info.is_dir() else pure.as_posix()


def extract_runtime(archive: Path, pin: runtime_pin.Pin, extra: dict, root: Path, crt_files: dict | None = None) -> Path:
    """crt_files: {member name: verified source path in <root>/crt/<version>/} for CRT DLLs pinned as runtime members."""
    llama = root / "llama"
    ensure_dir(llama, root)
    target = llama / pin.dir_name
    refuse_links(target, root)
    expected = {m.name: m for m in pin.members}
    licenses = {l["name"]: l["source"] for l in extra["runtime_licenses"]}
    crt_files = crt_files or {}
    if any(n not in expected for n in crt_files):
        raise PrepareError("pin_invalid")
    from_zip = {n for n in expected if n not in licenses and n not in crt_files}
    staging = llama / f".staging-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        with zipfile.ZipFile(archive) as zf:
            seen: set[str] = set()
            for info in zf.infolist():
                name = safe_member_name(info)
                if name is None:
                    continue
                folded = name.casefold()
                if folded in seen:
                    raise PrepareError("archive_unsafe", "duplicate " + name)
                seen.add(folded)
                if name not in from_zip:
                    continue  # official ZIP members outside the pinned subset are never written
                member = expected[name]
                if info.file_size != member.bytes:
                    raise PrepareError("download_integrity", name)
                dest = staging.joinpath(*name.split("/"))
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "xb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
            missing = from_zip - {n for n in expected if (staging.joinpath(*n.split("/"))).exists()}
            if missing:
                raise PrepareError("download_integrity", "missing " + ",".join(sorted(missing)))
        for name, source in licenses.items():
            src = (APP_ROOT / source).resolve()
            if APP_ROOT.resolve() not in src.parents:
                raise PrepareError("pin_invalid")
            shutil.copyfile(src, staging.joinpath(*name.split("/")))
        for name, src in crt_files.items():
            shutil.copyfile(src, staging.joinpath(*name.split("/")))
        for name, member in expected.items():
            path = staging.joinpath(*name.split("/"))
            if path.stat().st_size != member.bytes or sha256_file(path) != member.sha256:
                raise PrepareError("download_integrity", name)
        if target.exists():
            # an existing directory that does not verify is kept aside, never deleted
            aside = llama / f"{pin.dir_name}-before-update-{time.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
            os.replace(target, aside)
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return target


def runtime_verified(pin: runtime_pin.Pin, root: Path) -> bool:
    paths = runtime_pin.runtime_paths(pin, root)
    if not paths.runtime_dir.is_dir() or runtime_pin.linked(paths.runtime_dir, root):
        return False
    for member, path in paths.members:
        if runtime_pin.linked(path, root) or not path.is_file() or path.stat().st_size != member.bytes:
            return False
        if sha256_file(path) != member.sha256:
            return False
    return not runtime_pin.unlisted_binaries(paths)


def write_json_atomic(path: Path, data: dict):
    tmp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def prepare_runtime(pin, extra, root: Path, *, opener=open_url, crt_files: dict | None = None) -> dict:
    if runtime_verified(pin, root):
        return {"runtime": "already_verified"}
    archive = download(extra["runtime_url"], extra["runtime_hosts"], extra["runtime_bytes"], extra["runtime_sha256"],
                       root / "downloads", root, opener=opener)
    target = extract_runtime(archive, pin, extra, root, crt_files)
    if not runtime_verified(pin, root):
        raise PrepareError("install_failed")
    write_json_atomic(root / "llama" / f"{pin.dir_name}.receipt.json",
                      {"schema": 1, "pin_sha256": pin.sha256, "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                       "asset_sha256": extra["runtime_sha256"],
                       "files": {m.name: {"bytes": m.bytes, "sha256": m.sha256} for m in pin.members}})
    archive.unlink(missing_ok=True)
    return {"runtime": "installed", "dir": str(target)}


# --- model install ---------------------------------------------------------------------------------
def prepare_model(pin, extra, root: Path, *, opener=open_url) -> dict:
    models = root / "models"
    ensure_dir(models, root)
    paths = runtime_pin.runtime_paths(pin, root)
    model, marker = paths.model, paths.marker
    lic_dest = models / f"{pin.model_sha256}.LICENSE.txt"
    for p in (model, marker, lic_dest):
        refuse_links(p, root)
    lic_src = (APP_ROOT / extra["model_license_source"]).resolve()
    if APP_ROOT.resolve() not in lic_src.parents or sha256_file(lic_src) != extra["model_license_sha256"]:
        raise PrepareError("pin_invalid")
    result = {}
    # A marker never substitutes for verification here: the full file hash decides.
    if model.is_file() and model.stat().st_size == pin.model_bytes and sha256_file(model) == pin.model_sha256:
        result["model"] = "already_verified"
    else:
        if model.exists():
            aside = models / f"{pin.model_sha256}.gguf-unverified-{uuid.uuid4().hex[:8]}"
            os.replace(model, aside)
            result["previous_unverified_kept"] = aside.name
        got = download(extra["model_url"], extra["model_hosts"], pin.model_bytes, pin.model_sha256, root / "downloads", root,
                       opener=opener)
        os.replace(got, model)
        result["model"] = "installed"
    shutil.copyfile(lic_src, lic_dest)
    info = model.stat()
    write_json_atomic(marker, {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "sha256": pin.model_sha256,
                               "pin_sha": pin.sha256})
    return result


# --- app-local Microsoft C++ runtime (one consistent set for Python/ONNX and llama.cpp) ------------
def file_version(path: Path) -> str | None:
    """Windows VS_FIXEDFILEINFO file version 'a.b.c.d' (None if unavailable)."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    ver = ctypes.WinDLL("version")
    ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    ver.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    ver.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    n = ver.GetFileVersionInfoSizeW(str(path), None)
    if not n:
        return None
    buf = ctypes.create_string_buffer(n)
    if not ver.GetFileVersionInfoW(str(path), 0, n, buf):
        return None
    ptr, ln = ctypes.c_void_p(), wintypes.UINT()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)):
        return None
    f = ctypes.cast(ptr, ctypes.POINTER(wintypes.DWORD * 13)).contents
    return f"{f[2] >> 16}.{f[2] & 0xFFFF}.{f[3] >> 16}.{f[3] & 0xFFFF}"


MS_PACKAGE_HOSTS = {"download.visualstudio.microsoft.com"}
MS_TERMS_HOSTS = {"visualstudio.microsoft.com"}
LICENSE_NAME = "Microsoft-VC-Runtime-License.rtf"
LICENSE_DOCX = "Microsoft-VC-Runtime-License.docx"
LICENSE_TEXT = "Microsoft-VC-Runtime-License.txt"
PYTHON_STAGE = "python-stage"


def load_crt_section(pin_path: Path) -> dict | None:
    """Installer-only top-level 'crt' section (ignored by the backend loader): the official Microsoft
    VC_redist.x64 package that EACH end user downloads directly from Microsoft. Ryoshu ships no CRT binary."""
    raw = json.loads(Path(pin_path).read_bytes().decode("utf-8"))
    crt = raw.get("crt")
    if crt is None:
        return None
    try:
        if crt["source"] != "microsoft_vc_redist_direct":
            raise ValueError("source")
        pk = crt["package"]
        files = {f["name"]: {"bytes": _pos(f["bytes"]), "sha256": _hex64(f["sha256"]), "file_version": str(f["file_version"]),
                             "cab_entry": str(f["cab_entry"])} for f in crt["files"]}
        out = {"version": str(crt["version"]),
               "package": {"url": _https(pk["url"]), "bytes": _pos(pk["bytes"]), "sha256": _hex64(pk["sha256"]), "hosts": _hosts(pk["download_hosts"])},
               "burn": {k: {"offset": _pos(crt["burn"][k]["offset"]), "bytes": _pos(crt["burn"][k]["bytes"]), "sha256": _hex64(crt["burn"][k]["sha256"])}
                        for k in ("ux", "attached")},
               "license": _license_spec(crt["license"]),
               "cab": {"attached_entry": str(crt["cab"]["attached_entry"]), "bytes": _pos(crt["cab"]["bytes"]), "sha256": _hex64(crt["cab"]["sha256"])},
               "files": files}
        out["files_list"] = [{"name": n, **f} for n, f in files.items()]
    except (KeyError, TypeError, ValueError):
        raise PrepareError("pin_invalid") from None
    if set(files) != set(APP_CRT) or any(f["file_version"] != out["version"] for f in files.values()):
        raise PrepareError("pin_invalid")
    if not set(out["package"]["hosts"]) <= MS_PACKAGE_HOSTS or urllib.parse.urlsplit(out["package"]["url"]).hostname not in MS_PACKAGE_HOSTS:
        raise PrepareError("pin_invalid")  # only Microsoft's own download host; never a Ryoshu-hosted copy
    lic = out["license"]
    if "url" in lic and (not set(lic["hosts"]) <= MS_TERMS_HOSTS or urllib.parse.urlsplit(lic["url"]).hostname not in MS_TERMS_HOSTS):
        raise PrepareError("pin_invalid")
    return out


def _license_spec(lic: dict) -> dict:
    """Embedded RTF in the package UX (14.44 era), or Microsoft's official terms document that the current package
    links to instead of embedding (14.51+: no license payload). Either way the exact bytes are hash-pinned."""
    out = {"bytes": _pos(lic["bytes"]), "sha256": _hex64(lic["sha256"]), "eula_id": str(lic["eula_id"]),
           "title": str(lic.get("title", ""))}
    if "document_url" in lic:
        if lic.get("format") != "docx":
            raise ValueError("format")
        out.update(url=_https(lic["document_url"]), hosts=_hosts(lic["download_hosts"]), format="docx", name=LICENSE_DOCX)
    else:
        out.update(ux_entry=str(lic["ux_entry"]), format="rtf", name=LICENSE_NAME)
    return out


def _ms_spec(crt: dict) -> dict:
    """Shape expected by ms_vcredist (files as a list)."""
    return {"package": crt["package"], "burn": crt["burn"], "license": crt["license"], "cab": crt["cab"],
            "files": crt["files_list"]}


def crt_dir(root: Path, crt: dict) -> Path:
    return root / "crt" / crt["version"]


def terms_dir(root: Path, crt: dict) -> Path:
    return root / "crt" / f"terms-{crt['license']['sha256'][:16]}"


def consent_path(root: Path, crt: dict) -> Path:
    return root / "crt" / f"consent-{crt['version']}.json"


def consent_state(root: Path, crt: dict) -> str:
    """'accepted' / 'declined' only for a record bound to THIS exact EULA, package and version; else 'none'."""
    p = consent_path(root, crt)
    try:
        if runtime_pin.linked(p, root) or p.stat().st_size > 16384:
            return "none"
        rec = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "none"
    if (rec.get("eula_sha256") == crt["license"]["sha256"] and rec.get("package_sha256") == crt["package"]["sha256"]
            and rec.get("version") == crt["version"] and rec.get("decision") in ("accepted", "declined")):
        return rec["decision"]
    return "none"


def record_consent(root: Path, crt: dict, decision: str, method: str):
    ensure_dir(root / "crt", root)
    refuse_links(consent_path(root, crt), root)
    write_json_atomic(consent_path(root, crt), {
        "schema": 1, "decision": decision, "method": method, "decided_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "eula_id": crt["license"]["eula_id"], "eula_sha256": crt["license"]["sha256"],
        "package_url": crt["package"]["url"], "package_sha256": crt["package"]["sha256"], "version": crt["version"]})


def crt_verified(root: Path, crt: dict) -> bool:
    d = crt_dir(root, crt)
    if not d.is_dir() or runtime_pin.linked(d, root):
        return False
    expected = {**crt["files"], crt["license"]["name"]: crt["license"]}
    if {p.name for p in d.iterdir()} != set(expected):
        return False
    return all((d / n).stat().st_size == e["bytes"] and sha256_file(d / n) == e["sha256"] for n, e in expected.items())


def _package_bytes(crt: dict, root: Path, opener) -> bytes:
    p = crt["package"]
    path = download(p["url"], p["hosts"], p["bytes"], p["sha256"], root / "downloads", root, opener=opener)
    data = path.read_bytes()
    if len(data) != p["bytes"] or hashlib.sha256(data).hexdigest() != p["sha256"]:
        raise PrepareError("download_integrity")
    return data


def prepare_crt(crt: dict, root: Path, *, opener=open_url, accept_terms_sha: str | None = None, decline: bool = False,
                reconsider: bool = False, qa_test_acceptance: bool = False) -> Path:
    """Official Microsoft package -> exact license -> user's choice -> four verified DLLs in <root>/crt/<version>/.
    Never executes the package. Without an accepted record for this exact EULA, nothing is placed."""
    if decline:
        record_consent(root, crt, "declined", "explicit_decline")
        raise PrepareError("crt_declined")
    if crt_verified(root, crt) and consent_state(root, crt) == "accepted":
        return crt_dir(root, crt)
    pkg = _package_bytes(crt, root, opener)
    spec = crt["license"]
    try:
        if spec["format"] == "docx":
            p = download(spec["url"], spec["hosts"], spec["bytes"], spec["sha256"], root / "downloads", root, opener=opener)
            lic = ms_vcredist.verified(p.read_bytes(), spec, "license")
            text = ms_vcredist.docx_to_text(lic)
        else:
            lic = ms_vcredist.read_license(pkg, _ms_spec(crt))
            text = ms_vcredist.rtf_to_text(lic)
    except ms_vcredist.PackageError as exc:
        raise PrepareError("download_integrity" if exc.code == "package_integrity" else "archive_unsafe", exc.detail) from None
    tdir = terms_dir(root, crt)
    ensure_dir(tdir, root)
    for name, data in ((spec["name"], lic), (LICENSE_TEXT, text.encode("utf-8"))):
        refuse_links(tdir / name, root)
        tmp = tdir / f".{name}.{uuid.uuid4().hex}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, tdir / name)
    state = consent_state(root, crt)
    if state != "accepted":
        if accept_terms_sha is None:
            # A recorded decline stands, except when the user explicitly reruns setup (reconsider): then the
            # current terms are offered again. Nothing is ever accepted without a new explicit choice.
            raise PrepareError("crt_declined" if state == "declined" and not reconsider else "crt_terms_pending", {
                "previous_decision": state,
                "terms_text": str(tdir / LICENSE_TEXT), "terms_document": str(tdir / spec["name"]),
                "terms_url": spec.get("url", crt["package"]["url"]), "terms_title": spec["title"],
                "eula_id": crt["license"]["eula_id"], "eula_sha256": crt["license"]["sha256"], "version": crt["version"],
                "package_url": crt["package"]["url"], "package_sha256": crt["package"]["sha256"]})
        if accept_terms_sha != crt["license"]["sha256"]:
            raise PrepareError("crt_terms_mismatch")  # acceptance must name exactly the terms that were shown
        # A synthetic CI/QA acceptance is recorded as such and is never presented as a person's choice.
        record_consent(root, crt, "accepted", "qa_test_flag" if qa_test_acceptance else "explicit_accept")
    try:
        files = ms_vcredist.read_runtime_files(pkg, _ms_spec(crt))
    except ms_vcredist.PackageError as exc:
        raise PrepareError("download_integrity" if exc.code == "package_integrity" else "archive_unsafe", exc.detail) from None
    base = root / "crt"
    staging = base / f".staging-{uuid.uuid4().hex}"
    ensure_dir(staging, root)
    try:
        for n, data in files.items():
            (staging / n).write_bytes(data)
            if os.name == "nt" and file_version(staging / n) != crt["files"][n]["file_version"]:
                raise PrepareError("crt_version_mismatch", n)
        (staging / spec["name"]).write_bytes(lic)
        target = crt_dir(root, crt)
        refuse_links(target, root)
        if target.exists():
            os.replace(target, base / f"{crt['version']}-before-update-{uuid.uuid4().hex[:8]}")
        os.replace(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    (root / "downloads" / f"{crt['package']['sha256']}.bin").unlink(missing_ok=True)
    (root / "downloads" / f"{spec['sha256']}.bin").unlink(missing_ok=True)
    return target


def python_owned(root: Path, python_base: Path) -> bool:
    """The raw (unresolved) interpreter base lies strictly inside <root>\\python\\, the app-managed install dir."""
    base = os.path.normcase(str(runtime_pin.absolute(python_base)))
    home = os.path.normcase(str(runtime_pin.absolute(Path(root) / "python")))
    try:
        return base != home and os.path.commonpath([base, home]) == home
    except ValueError:
        return False


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except (AttributeError, ValueError):
        return ()


def install_python_crt(crt: dict, root: Path, python_base: Path, source_pin_sha256: str) -> dict:
    """Make the app-managed Python load ONE consistent CRT: all four pinned DLLs in its application directory.
    This helper runs ON that interpreter, whose vcruntime140 is mapped (cannot be overwritten), so nothing is
    written into the interpreter here: verified copies plus a plan go to <root>\\crt\\python-stage\\ and setup
    applies them after this process exits (Invoke-RyoshuPythonCrtStage), keeping the replaced bytes as a backup.
    Never downgrades: a newer interpreter-owned vcruntime is refused rather than replaced."""
    python_base = runtime_pin.absolute(python_base)
    if not python_owned(root, python_base) or runtime_pin.linked_ancestry(python_base):
        raise PrepareError("python_not_owned")
    for n in PYTHON_OWN_CRT:
        own = python_base / n
        if not own.is_file() or runtime_pin.is_link(own):
            raise PrepareError("crt_python_unexpected", n)
        v = file_version(own)
        if os.name == "nt" and _version_tuple(v) > _version_tuple(crt["version"]):
            raise PrepareError("crt_version_mismatch", f"python {n} {v} newer than pinned {crt['version']}")
    current = {n: (sha256_file(python_base / n) if (python_base / n).is_file() else None) for n in APP_CRT}
    if any(runtime_pin.is_link(python_base / n) for n in APP_CRT):
        raise PrepareError("link_refused")
    if all(current[n] == crt["files"][n]["sha256"] for n in APP_CRT):
        return {"python": "current", **{n: "already_present" for n in APP_CRT}}
    src = crt_dir(root, crt)
    stage = root / "crt" / PYTHON_STAGE
    refuse_links(stage, root)
    if stage.exists():
        os.replace(stage, root / "crt" / f"{PYTHON_STAGE}-superseded-{uuid.uuid4().hex[:8]}")
    tmp = root / "crt" / f".{PYTHON_STAGE}-{uuid.uuid4().hex}"
    ensure_dir(tmp, root)
    for n in APP_CRT:
        shutil.copyfile(src / n, tmp / n)
        if sha256_file(tmp / n) != crt["files"][n]["sha256"]:
            shutil.rmtree(tmp, ignore_errors=True)
            raise PrepareError("install_failed", n)
    write_json_atomic(tmp / "plan.json", {
        # setup re-verifies every value against ITS OWN source pin before applying; this copy is not the authority
        "schema": 1, "source_pin_sha256": source_pin_sha256, "version": crt["version"], "python_base": str(python_base), "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files": {n: {"bytes": crt["files"][n]["bytes"], "sha256": crt["files"][n]["sha256"], "current_sha256": current[n]} for n in APP_CRT}})
    os.replace(tmp, stage)
    return {"python": "staged", "stage": str(stage), **{n: ("already_present" if current[n] == crt["files"][n]["sha256"] else "staged")
                                                        for n in APP_CRT}}


def crt_closure_state(python_base: Path | None, system_root: str | None = None) -> dict:
    """Without an adopted Ryoshu CRT artifact: report exactly where each needed DLL would come from."""
    sys32 = Path(system_root or os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    state = {}
    for n in APP_CRT:
        local = Path(python_base) / n if python_base else None
        state[n] = ("python_local" if local and local.is_file() else
                    "system32" if (sys32 / n).is_file() else "missing")
    return state


# --- preflight -------------------------------------------------------------------------------------
def vc_runtime_present(system_root: str | None = None) -> list[str]:
    """Names of the Microsoft C++ runtime DLLs required by the official CPU build that are absent from System32."""
    sys32 = Path(system_root or os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return [n for n in VC_RUNTIME if not (sys32 / n).is_file()]


def prepare_crt_only(root: Path, pin_path: Path, python_base: Path, *, opener=open_url, system_root: str | None = None,
                     accept_terms_sha: str | None = None, decline: bool = False, reconsider: bool = False,
                     qa_test_acceptance: bool = False) -> dict:
    """Before OCR (onnxruntime) is first imported: establish ONE consistent CRT for the managed Python from the
    official Microsoft package, only after the user's explicit acceptance of the exact runtime license.
    Pending/declined results carry the license identity and the current closure state for setup to act on."""
    root = safe_root(root)
    python_base = runtime_pin.absolute(python_base)
    if not python_owned(root, python_base):
        raise PrepareError("python_not_owned")  # never write into a shared or unrelated interpreter (BP02)
    if runtime_pin.linked_ancestry(python_base):
        raise PrepareError("link_refused")
    load_install_pin(pin_path)
    crt = load_crt_section(pin_path)
    if crt is None:
        state = crt_closure_state(python_base, system_root)
        if "missing" in state.values():
            raise PrepareError("crt_unavailable", ",".join(n for n, s in state.items() if s == "missing"))
        return {"crt": "system_or_python_local_unmanaged", "closure": state}
    try:
        prepare_crt(crt, root, opener=opener, accept_terms_sha=accept_terms_sha, decline=decline, reconsider=reconsider,
                    qa_test_acceptance=qa_test_acceptance)
    except PrepareError as exc:
        if exc.code in ("crt_terms_pending", "crt_declined"):
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            detail["closure"] = crt_closure_state(python_base, system_root)
            raise PrepareError(exc.code, detail) from None
        raise
    return {"crt": "app_local", "version": crt["version"],
            **install_python_crt(crt, root, python_base, sha256_file(Path(pin_path)))}


def prepare(root: Path, pin_path: Path, *, opener=open_url, system_root: str | None = None) -> dict:
    root = safe_root(root)
    pin, extra = load_install_pin(pin_path)
    crt = load_crt_section(pin_path)
    crt_files = None
    if crt is None:
        # No adopted Ryoshu CRT artifact: the official build can only run where the OS already provides it.
        missing_vc = vc_runtime_present(system_root)
        if missing_vc:
            raise PrepareError("vc_runtime_missing", ",".join(missing_vc))
    else:
        cdir = prepare_crt(crt, root, opener=opener)
        members = {m.name for m in pin.members}
        crt_files = {n: cdir / n for n in crt["files"] if n in members}
        if set(VC_RUNTIME) - set(crt_files):
            raise PrepareError("pin_invalid")  # the runtime must carry its own CRT members once a CRT is adopted
    out = {"pin_sha256": pin.sha256}
    out.update(prepare_runtime(pin, extra, root, opener=opener, crt_files=crt_files))
    out.update(prepare_model(pin, extra, root, opener=opener))
    state = runtime_pin.ready_state(pin_path, root)
    if not (state["runtime_ok"] and state["model_ok"]):
        raise PrepareError("install_failed", state["reason_code"])
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prepare the pinned local inference runtime and model.")
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--pin", type=Path, default=APP_ROOT / "receipt_ledger" / "runtime_pin.json")
    ap.add_argument("--crt-only", action="store_true", help="establish the Python/ONNX C++ runtime closure only")
    ap.add_argument("--python-base", type=Path, help="managed interpreter directory (sys.base_prefix)")
    choice = ap.add_mutually_exclusive_group()
    choice.add_argument("--accept-crt-terms", metavar="EULA_SHA256",
                        help="the user accepted exactly these Microsoft runtime license terms (SHA-256 of the shown RTF)")
    choice.add_argument("--decline-crt-terms", action="store_true", help="the user declined the Microsoft runtime license terms")
    choice.add_argument("--reconsider-crt-terms", action="store_true",
                        help="explicit setup rerun: offer the current terms again even after a recorded decline (never accepts)")
    ap.add_argument("--qa-test-acceptance", action="store_true",
                    help="synthetic QA/CI only: record --accept-crt-terms as method 'qa_test_flag', never as a person's choice")
    a = ap.parse_args(argv)
    if a.qa_test_acceptance and not a.accept_crt_terms:
        ap.error("--qa-test-acceptance requires --accept-crt-terms")
    a.root = runtime_pin.absolute(a.root)  # lexical absolute: a relative path works, links stay visible
    result_path = a.root / "runtime" / ("crt-setup-result.json" if a.crt_only else "llama-setup-result.json")
    try:
        if a.crt_only:
            if not a.python_base:
                raise PrepareError("pin_invalid")
            out = prepare_crt_only(a.root, a.pin, a.python_base, accept_terms_sha=a.accept_crt_terms, decline=a.decline_crt_terms,
                                   reconsider=a.reconsider_crt_terms, qa_test_acceptance=a.qa_test_acceptance)
        else:
            out = prepare(a.root, a.pin)
        res = {"ok": True, "reason_code": "", **out}
        code = 0
    except PrepareError as exc:
        res = {"ok": False, "reason_code": exc.code}
        if exc.code in ("crt_terms_pending", "crt_declined") and isinstance(exc.detail, dict):
            res.update(exc.detail)  # license identity/paths + closure for setup's display and decision
        code = 3
    except Exception as exc:  # unexpected: report a code, never a traceback with paths/URLs
        res = {"ok": False, "reason_code": "install_failed", "error_type": type(exc).__name__}
        code = 4
    # The result file is written only below a root whose ancestry and runtime\ component are link-free;
    # an unsafe root still reports its code on stdout but nothing is created or written through it.
    try:
        if not runtime_pin.linked_ancestry(a.root) and not runtime_pin.is_link(result_path.parent):
            runtime_pin.safe_mkdir(result_path.parent, a.root)
            if not runtime_pin.linked(result_path, a.root):
                write_json_atomic(result_path, res)
    except OSError:
        pass
    print(json.dumps({"ok": res["ok"], "reason_code": res["reason_code"]}))
    return code


if __name__ == "__main__":
    sys.exit(main())
