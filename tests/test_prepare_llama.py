"""scripts/prepare_llama.py: pinned runtime/model preparation without network access.

A fake opener serves small synthetic artifacts; pins written per test point at them.
The real product pin is checked separately for structure, licenses and no placeholders.
"""
import hashlib
import io
import json
import os
import shutil
import stat
import struct
import sys
import zipfile
import zlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import prepare_llama as pl  # noqa: E402
from receipt_ledger import runtime_pin  # noqa: E402

MIT = (ROOT / "third_party/llama.cpp/LICENSE").read_bytes()
QWEN = (ROOT / "third_party/Qwen3-4B-GGUF/LICENSE").read_bytes()
H40 = "0" * 39 + "1"
sha = lambda b: hashlib.sha256(b).hexdigest()


def make_zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data, *attr in entries:
            info = zipfile.ZipInfo(name)
            info.filename = name  # keep raw bytes: ZipInfo() on Windows rewrites '\\' to '/'
            if attr:
                info.external_attr = attr[0]
            zf.writestr(info, data)
    return buf.getvalue()


EXE = b"MZ fake llama-server"
DLL = b"MZ fake ggml"
GOOD_ZIP = make_zip([("llama-server.exe", EXE), ("ggml.dll", DLL), ("llama-bench.exe", b"MZ not pinned"),
                     ("ggml-rpc.dll", b"MZ not pinned rpc")])
MODEL = b"GGUF" + b"\x00" * 4092


def write_pin(tmp, zip_bytes=GOOD_ZIP, model=MODEL, *, members=None, hosts=None, **overrides):
    members = members or [{"name": "llama-server.exe", "bytes": len(EXE), "sha256": sha(EXE)},
                          {"name": "ggml.dll", "bytes": len(DLL), "sha256": sha(DLL)},
                          {"name": "LICENSE-llama.cpp.txt", "bytes": len(MIT), "sha256": sha(MIT)}]
    pin = {"schema": 1,
           "runtime": {"tag": "b0", "commit": H40, "dir_name": "b0-cpu-x64", "server_exe": "llama-server.exe",
                       "members": members, "asset_url": "https://github.com/x/y.zip", "asset_bytes": len(zip_bytes),
                       "asset_sha256": sha(zip_bytes), "download_hosts": hosts or ["github.com", "release-assets.githubusercontent.com"],
                       "license_files": [{"name": "LICENSE-llama.cpp.txt", "source": "third_party/llama.cpp/LICENSE"}]},
           "model": {"repo": "Qwen/x", "revision": H40, "file": "m.gguf", "bytes": len(model), "sha256": sha(model),
                     "license": "Apache-2.0", "chat_template_sha256": "a" * 64, "url": "https://huggingface.co/x/m.gguf",
                     "download_hosts": ["huggingface.co", "us.aws.cdn.hf.co"],
                     "license_file": {"source": "third_party/Qwen3-4B-GGUF/LICENSE", "sha256": sha(QWEN)}}}
    for path, value in overrides.items():
        section, key = path.split("__")
        pin[section][key] = value
    p = tmp / "runtime_pin.json"
    p.write_text(json.dumps(pin), encoding="utf-8")
    return p


class Resp(io.BytesIO):
    def __init__(self, data, status=200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class Server:
    """Serves bytes by URL; records Range starts; can ignore Range or truncate."""

    def __init__(self, files, *, ignore_range=False, truncate=None):
        self.files, self.ignore_range, self.truncate, self.calls = files, ignore_range, truncate, []

    def __call__(self, url, allowed, hops, start=0, timeout=60):
        host = url.split("/")[2]
        if not pl.host_allowed(host, allowed):
            raise pl.PrepareError("download_host_refused")
        self.calls.append((url, start))
        data = self.files[url]
        if self.truncate:
            data = data[: self.truncate]
        if start and not self.ignore_range:
            return Resp(data[start:], 206)
        return Resp(data, 200)


@pytest.fixture
def sysroot(tmp_path):
    s = tmp_path / "Windows"
    (s / "System32").mkdir(parents=True)
    for n in pl.APP_CRT:
        (s / "System32" / n).write_bytes(b"MZ")
    return s


@pytest.fixture
def task_root(tmp_path):
    r = tmp_path / "profile 日本語 ' & space" / "StudioYebisu" / "ReceiptLedger"
    r.mkdir(parents=True)
    return r


def serve(zip_bytes=GOOD_ZIP, model=MODEL, **kw):
    return Server({"https://github.com/x/y.zip": zip_bytes, "https://huggingface.co/x/m.gguf": model}, **kw)


def test_happy_path_installs_only_pinned_members_and_is_idempotent(tmp_path, task_root, sysroot):
    pin = write_pin(tmp_path)
    srv = serve()
    out = pl.prepare(task_root, pin, opener=srv, system_root=str(sysroot))
    assert out["runtime"] == "installed" and out["model"] == "installed"
    rt = task_root / "llama/b0-cpu-x64"
    assert sorted(p.name for p in rt.iterdir()) == ["LICENSE-llama.cpp.txt", "ggml.dll", "llama-server.exe"]
    assert (rt / "LICENSE-llama.cpp.txt").read_bytes() == MIT  # exact legal text, copyright preserved
    model = task_root / "models" / (sha(MODEL) + ".gguf")
    marker = json.loads((model.parent / (model.name + ".verified.json")).read_text("utf-8"))
    assert set(marker) == {"size", "mtime_ns", "sha256", "pin_sha"} and marker["sha256"] == sha(MODEL)
    assert (task_root / "models" / (sha(MODEL) + ".LICENSE.txt")).read_bytes() == QWEN
    receipt = json.loads((task_root / "llama/b0-cpu-x64.receipt.json").read_text("utf-8"))
    assert receipt["pin_sha256"] == runtime_pin.load_pin(pin).sha256
    assert list((task_root / "downloads").iterdir()) == []
    assert runtime_pin.ready_state(pin, task_root)["reason_code"] == ""
    calls = len(srv.calls)
    again = pl.prepare(task_root, pin, opener=srv, system_root=str(sysroot))
    assert again["runtime"] == "already_verified" and again["model"] == "already_verified"
    assert len(srv.calls) == calls  # nothing downloaded again


def test_resume_uses_range_and_restarts_when_range_is_ignored(tmp_path, task_root, sysroot):
    pin = write_pin(tmp_path)
    dl = task_root / "downloads"
    dl.mkdir()
    (dl / (sha(GOOD_ZIP) + ".part")).write_bytes(GOOD_ZIP[:50])
    srv = serve()
    pl.prepare(task_root, pin, opener=srv, system_root=str(sysroot))
    assert ("https://github.com/x/y.zip", 50) in srv.calls
    shutil.rmtree(task_root / "llama")
    (dl / (sha(GOOD_ZIP) + ".part")).write_bytes(GOOD_ZIP[:50])
    ignoring = serve(ignore_range=True)
    pl.prepare(task_root, pin, opener=ignoring, system_root=str(sysroot))
    assert runtime_pin.ready_state(pin, task_root)["runtime_ok"]


def test_hash_mismatch_discards_part_and_installs_nothing(tmp_path, task_root, sysroot):
    pin = write_pin(tmp_path)
    bad = bytearray(GOOD_ZIP)
    bad[-1] ^= 1
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(task_root, pin, opener=serve(zip_bytes=bytes(bad)), system_root=str(sysroot))
    assert e.value.code == "download_integrity"
    assert not (task_root / "llama/b0-cpu-x64").exists()
    assert not list((task_root / "downloads").glob("*.part"))


def test_short_download_keeps_part_for_resume(tmp_path, task_root, sysroot):
    pin = write_pin(tmp_path)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(task_root, pin, opener=serve(truncate=40), system_root=str(sysroot))
    assert e.value.code == "download_failed"
    assert (task_root / "downloads" / (sha(GOOD_ZIP) + ".part")).stat().st_size == 40


@pytest.mark.parametrize("host,ok", [("github.com", True), ("release-assets.githubusercontent.com", True),
                                     ("evil.githubusercontent.com", False), ("github.com.evil.example", False),
                                     ("us.aws.cdn.hf.co", False)])
def test_host_allow_list_is_exact(host, ok):
    assert pl.host_allowed(host, ["github.com", "release-assets.githubusercontent.com"]) is ok


def test_unpinned_host_is_refused_before_any_request(tmp_path, task_root, sysroot):
    pin = write_pin(tmp_path, runtime__asset_url="https://evil.example/y.zip")
    srv = Server({"https://evil.example/y.zip": GOOD_ZIP})
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(task_root, pin, opener=pl.open_url, system_root=str(sysroot))
    assert e.value.code == "download_host_refused" and srv.calls == []


@pytest.mark.parametrize("url", ["http://github.com/x/y.zip", "https://github.com/x/y.zip?token=1", "https://user@github.com/x.zip"])
def test_pin_rejects_non_https_query_or_credential_urls(tmp_path, task_root, sysroot, url):
    pin = write_pin(tmp_path, runtime__asset_url=url)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(task_root, pin, opener=serve(), system_root=str(sysroot))
    assert e.value.code == "pin_invalid"


@pytest.mark.parametrize("entries", [
    [("../evil.dll", b"x")], [("/abs.dll", b"x")], [("dir\\back.dll", b"x")], [("c:evil.dll", b"x")],
    [("llama-server.exe", EXE), ("LLAMA-SERVER.EXE", EXE)],
    [("link.dll", b"target", (stat.S_IFLNK | 0o777) << 16)],
], ids=["traversal", "absolute", "backslash", "drive-or-ads", "case-duplicate", "symlink"])
def test_malicious_archives_are_rejected_without_installing(tmp_path, task_root, sysroot, entries):
    z = make_zip([("llama-server.exe", EXE), ("ggml.dll", DLL)] + entries if entries[0][0] != "llama-server.exe" else entries + [("ggml.dll", DLL)])
    pin = write_pin(tmp_path, zip_bytes=z)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(task_root, pin, opener=serve(zip_bytes=z), system_root=str(sysroot))
    assert e.value.code == "archive_unsafe"
    assert not (task_root / "llama/b0-cpu-x64").exists()
    assert not list((task_root / "llama").glob(".staging-*"))


def test_failure_preserves_previous_working_runtime_and_ledger(tmp_path, task_root, sysroot):
    pin = write_pin(tmp_path)
    pl.prepare(task_root, pin, opener=serve(), system_root=str(sysroot))
    ledger = task_root / "data/ledger.enc.sqlite3"
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b"ledger-fixture")
    before = {p.name: p.read_bytes() for p in (task_root / "llama/b0-cpu-x64").iterdir()}
    exe2 = b"MZ new server"
    z2 = make_zip([("llama-server.exe", exe2), ("ggml.dll", DLL)])
    pin2 = write_pin(tmp_path, zip_bytes=z2, members=[
        {"name": "llama-server.exe", "bytes": len(exe2), "sha256": sha(exe2)},
        {"name": "ggml.dll", "bytes": len(DLL), "sha256": sha(DLL)},
        {"name": "LICENSE-llama.cpp.txt", "bytes": len(MIT), "sha256": sha(MIT)}], runtime__dir_name="b1-cpu-x64")
    corrupt = bytearray(z2)
    corrupt[-1] ^= 1
    with pytest.raises(pl.PrepareError):
        pl.prepare(task_root, pin2, opener=serve(zip_bytes=bytes(corrupt)), system_root=str(sysroot))
    assert {p.name: p.read_bytes() for p in (task_root / "llama/b0-cpu-x64").iterdir()} == before
    assert not (task_root / "llama/b1-cpu-x64").exists()
    assert ledger.read_bytes() == b"ledger-fixture"


def test_existing_invalid_runtime_dir_is_kept_aside_not_deleted(tmp_path, task_root, sysroot):
    pin = write_pin(tmp_path)
    rt = task_root / "llama/b0-cpu-x64"
    rt.mkdir(parents=True)
    (rt / "llama-server.exe").write_bytes(b"tampered")
    pl.prepare(task_root, pin, opener=serve(), system_root=str(sysroot))
    aside = list((task_root / "llama").glob("b0-cpu-x64-before-update-*"))
    assert len(aside) == 1 and (aside[0] / "llama-server.exe").read_bytes() == b"tampered"
    assert (rt / "llama-server.exe").read_bytes() == EXE


def test_marker_cannot_make_a_wrong_model_acceptable(tmp_path, task_root, sysroot):
    pin = write_pin(tmp_path)
    models = task_root / "models"
    models.mkdir()
    wrong = models / (sha(MODEL) + ".gguf")
    wrong.write_bytes(b"X" * len(MODEL))  # right size, wrong bytes
    info = wrong.stat()
    (models / (wrong.name + ".verified.json")).write_text(json.dumps(
        {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "sha256": sha(MODEL), "pin_sha": "x"}), "utf-8")
    out = pl.prepare(task_root, pin, opener=serve(), system_root=str(sysroot))
    assert out["model"] == "installed" and "previous_unverified_kept" in out
    assert wrong.read_bytes() == MODEL


def test_missing_vc_runtime_fails_before_any_download(tmp_path, task_root, sysroot):
    (sysroot / "System32" / "msvcp140.dll").unlink()
    pin = write_pin(tmp_path)
    srv = serve()
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(task_root, pin, opener=srv, system_root=str(sysroot))
    assert e.value.code == "vc_runtime_missing" and srv.calls == []
    assert not (task_root / "llama").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction")
def test_junctioned_llama_directory_is_refused(tmp_path, task_root, sysroot):
    import _winapi
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    _winapi.CreateJunction(str(elsewhere), str(task_root / "llama"))
    pin = write_pin(tmp_path)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(task_root, pin, opener=serve(), system_root=str(sysroot))
    assert e.value.code == "link_refused"
    assert list(elsewhere.iterdir()) == []


@pytest.mark.parametrize("override", [{"model__sha256": "<OUTSTANDING>"}, {"runtime__asset_bytes": 0},
                                      {"runtime__download_hosts": []}])
def test_placeholder_or_invalid_pin_is_refused(tmp_path, task_root, sysroot, override):
    pin = write_pin(tmp_path, **override)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(task_root, pin, opener=serve(), system_root=str(sysroot))
    assert e.value.code == "pin_invalid"


def test_main_reports_code_without_traceback_and_writes_result(tmp_path, task_root, capsys, monkeypatch):
    monkeypatch.setattr(pl, "vc_runtime_present", lambda *a: ["msvcp140.dll"])
    code = pl.main(["--root", str(task_root), "--pin", str(write_pin(tmp_path))])
    assert code == 3
    assert json.loads(capsys.readouterr().out) == {"ok": False, "reason_code": "vc_runtime_missing"}
    res = json.loads((task_root / "runtime/llama-setup-result.json").read_text("utf-8"))
    assert res == {"ok": False, "reason_code": "vc_runtime_missing"}


# --- direct Microsoft package (synthetic Burn bundle; file_version patched) ------------------------
CRT_V = "14.44.35211.0"
CRT_FILES = {n: (b"MZ crt " + n.encode()) for n in pl.APP_CRT}
CRT_LICENSE = b"{\\rtf1{\\fonttbl{\\f0 Tahoma;}}\\pard MICROSOFT SOFTWARE LICENSE TERMS fixture\\par}"
MS_URL = "https://download.visualstudio.microsoft.com/download/pr/fixture/VC_redist.x64.exe"


def make_cab(entries, *, comp=1, block=32768, unsafe_name=None):
    """Minimal single-folder CAB (MSZIP or stored), as Microsoft's packages use."""
    stream = b"".join(d for _, d in entries)
    blocks = []
    for i in range(0, max(len(stream), 1), block):
        chunk = stream[i:i + block]
        if comp == 1:
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            data = b"CK" + c.compress(chunk) + c.flush()
        else:
            data = chunk
        blocks.append((data, len(chunk)))
    files, off = [], 0
    for name, d in entries:
        files.append(struct.pack("<IIHHHH", len(d), off, 0, 0, 0, 0x20) + (unsafe_name or name).encode("latin-1") + b"\0")
        off += len(d)
        unsafe_name = None if unsafe_name else None
    coff_files = 36 + 8
    coff_data = coff_files + sum(len(f) for f in files)
    data_part = b"".join(struct.pack("<IHH", 0, len(b), u) + b for b, u in blocks)
    total = coff_data + len(data_part)
    hdr = b"MSCF" + struct.pack("<IIIIIBBHHHHH", 0, total, 0, coff_files, 0, 3, 1, 1, len(entries), 0, 0, 0)
    folder = struct.pack("<IHH", coff_data, len(blocks), comp)
    return hdr + folder + b"".join(files) + data_part


def make_bundle(ux, attached, sig=b"S" * 40):
    """PE stub with a '.wixburn' section; UX container at stub end, signature region, then attached container."""
    pe_off = 0x40
    stub_len = 0x200
    head = bytearray(stub_len)
    head[0:2] = b"MZ"
    struct.pack_into("<I", head, 0x3C, pe_off)
    head[pe_off:pe_off + 4] = b"PE\0\0"
    struct.pack_into("<HH", head, pe_off + 4, 0x8664, 1)
    struct.pack_into("<H", head, pe_off + 20, 0)  # SizeOfOptionalHeader
    sec = pe_off + 24
    burn_at = 0x180
    head[sec:sec + 8] = b".wixburn"
    struct.pack_into("<IIII", head, sec + 8, 64, 0x1000, 64, burn_at)
    sig_off = stub_len + len(ux)
    struct.pack_into("<II", head, burn_at, 0x00F14300, 2)
    struct.pack_into("<6I", head, burn_at + 24, stub_len, 0, sig_off, len(sig), 1, 2)
    struct.pack_into("<2I", head, burn_at + 48, len(ux), len(attached))
    return bytes(head) + ux + sig + attached, {"ux": stub_len, "attached": sig_off + len(sig)}


def build_package(*, files=None, license_bytes=CRT_LICENSE, inner_comp=1, unsafe=None):
    files = files or CRT_FILES
    inner = make_cab([(n + "_amd64", files[n]) for n in sorted(files)], comp=inner_comp, unsafe_name=unsafe)
    attached = make_cab([("a12", inner)])
    ux = make_cab([("0", b"<manifest/>"), ("u4", license_bytes)])
    pkg, offs = make_bundle(ux, attached)
    return pkg, ux, attached, inner, offs


def make_docx(paragraphs, doctype=False):
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<!DOCTYPE x [<!ENTITY e "x">]>' if doctype else "") + f'<w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def write_crt_pin(tmp, pkg=None, *, runtime_crt=("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"), crt_override=None):
    built = build_package()
    pkg_bytes, ux, attached, inner, offs = built if pkg is None else (pkg, *build_package()[1:])
    members = [{"name": "llama-server.exe", "bytes": len(EXE), "sha256": sha(EXE)},
               {"name": "ggml.dll", "bytes": len(DLL), "sha256": sha(DLL)},
               {"name": "LICENSE-llama.cpp.txt", "bytes": len(MIT), "sha256": sha(MIT)}]
    members += [{"name": n, "bytes": len(CRT_FILES[n]), "sha256": sha(CRT_FILES[n])} for n in runtime_crt]
    p = write_pin(tmp, members=members)
    pin = json.loads(p.read_text("utf-8"))
    pin["crt"] = {"source": "microsoft_vc_redist_direct", "version": CRT_V,
                  "package": {"url": MS_URL, "bytes": len(pkg_bytes), "sha256": sha(pkg_bytes), "download_hosts": ["download.visualstudio.microsoft.com"]},
                  "burn": {"ux": {"offset": offs["ux"], "bytes": len(ux), "sha256": sha(ux)},
                           "attached": {"offset": offs["attached"], "bytes": len(attached), "sha256": sha(attached)}},
                  "license": {"ux_entry": "u4", "bytes": len(CRT_LICENSE), "sha256": sha(CRT_LICENSE), "eula_id": "Cpp_fixture"},
                  "cab": {"attached_entry": "a12", "bytes": len(inner), "sha256": sha(inner)},
                  "files": [{"name": n, "cab_entry": n + "_amd64", "bytes": len(b), "sha256": sha(b), "file_version": CRT_V}
                            for n, b in CRT_FILES.items()]}
    if crt_override:
        crt_override(pin["crt"])
    p.write_text(json.dumps(pin), "utf-8")
    return p, pkg_bytes


@pytest.fixture
def python_base(task_root, monkeypatch):
    base = task_root / "python/cpython-3.12.15-windows-x86_64-none"
    base.mkdir(parents=True)
    for n in pl.PYTHON_OWN_CRT:
        (base / n).write_bytes(b"MZ python own " + n.encode())
    versions = {}

    def fake_version(path):
        return versions.get(Path(path).name, CRT_V)

    monkeypatch.setattr(pl, "file_version", fake_version)
    return base, versions


def crt_server(pkg_bytes, zip_bytes=GOOD_ZIP):
    return Server({"https://github.com/x/y.zip": zip_bytes, "https://huggingface.co/x/m.gguf": MODEL, MS_URL: pkg_bytes})


def test_terms_are_shown_before_anything_is_placed_then_exact_acceptance_installs(tmp_path, task_root, sysroot, python_base):
    base, _ = python_base
    own_before = {n: (base / n).read_bytes() for n in pl.PYTHON_OWN_CRT}
    pin, pkg = write_crt_pin(tmp_path)
    srv = crt_server(pkg)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, base, opener=srv, system_root=str(tmp_path / "no-windows"))
    assert e.value.code == "crt_terms_pending"
    d = e.value.detail
    assert d["eula_sha256"] == sha(CRT_LICENSE) and d["package_sha256"] == sha(pkg) and d["package_url"] == MS_URL
    assert Path(d["terms_document"]).read_bytes() == CRT_LICENSE  # exact received license bytes
    assert "MICROSOFT SOFTWARE LICENSE TERMS fixture" in Path(d["terms_text"]).read_text("utf-8")
    assert "Tahoma" not in Path(d["terms_text"]).read_text("utf-8")
    assert d["closure"]["msvcp140.dll"] == "missing"
    assert not (task_root / "crt" / CRT_V).exists() and not (base / "msvcp140.dll").exists()  # nothing placed yet
    out = pl.prepare_crt_only(task_root, pin, base, opener=srv, system_root=str(tmp_path / "no-windows"),
                              accept_terms_sha=sha(CRT_LICENSE))
    assert out["crt"] == "app_local"
    rec = json.loads((task_root / "crt" / f"consent-{CRT_V}.json").read_text("utf-8"))
    assert rec["decision"] == "accepted" and rec["eula_sha256"] == sha(CRT_LICENSE) and rec["package_sha256"] == sha(pkg)
    # the helper runs ON this interpreter: nothing is written into it; a verified stage waits for setup to apply
    assert out["python"] == "staged" and {n: (base / n).read_bytes() for n in pl.PYTHON_OWN_CRT} == own_before
    assert not (base / "msvcp140.dll").exists()
    stage = task_root / "crt" / pl.PYTHON_STAGE
    plan = json.loads((stage / "plan.json").read_text("utf-8"))
    assert plan["python_base"] == str(base) and plan["version"] == CRT_V
    assert plan["source_pin_sha256"] == sha(pin.read_bytes())  # setup re-checks the stage against this source pin
    for n in pl.APP_CRT:
        assert (stage / n).read_bytes() == CRT_FILES[n] and plan["files"][n]["sha256"] == sha(CRT_FILES[n])
    assert plan["files"]["vcruntime140.dll"]["current_sha256"] == sha(own_before["vcruntime140.dll"])
    assert plan["files"]["msvcp140.dll"]["current_sha256"] is None
    full = pl.prepare(task_root, pin, opener=srv, system_root=str(tmp_path / "no-windows"))
    rt = task_root / "llama/b0-cpu-x64"
    for n in ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        assert (rt / n).read_bytes() == CRT_FILES[n]
    assert runtime_pin.ready_state(pin, task_root)["reason_code"] == "" and full["runtime"] == "installed"
    assert sorted(p.name for p in (task_root / "crt" / CRT_V).iterdir()) == sorted([*CRT_FILES, pl.LICENSE_NAME])
    assert not (task_root / "downloads" / f"{sha(pkg)}.bin").exists()


def test_acceptance_must_name_the_shown_terms(tmp_path, task_root, python_base):
    base, _ = python_base
    pin, pkg = write_crt_pin(tmp_path)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, base, opener=crt_server(pkg), accept_terms_sha="f" * 64)
    assert e.value.code == "crt_terms_mismatch"
    assert not (task_root / "crt" / f"consent-{CRT_V}.json").exists() and not (base / "msvcp140.dll").exists()


def test_decline_is_recorded_places_nothing_and_full_prepare_stays_unavailable(tmp_path, task_root, sysroot, python_base):
    base, _ = python_base
    pin, pkg = write_crt_pin(tmp_path)
    srv = crt_server(pkg)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, base, opener=srv, decline=True)
    assert e.value.code == "crt_declined" and "closure" in e.value.detail
    assert json.loads((task_root / "crt" / f"consent-{CRT_V}.json").read_text("utf-8"))["decision"] == "declined"
    with pytest.raises(pl.PrepareError) as again:  # a later run without a new choice keeps the decline
        pl.prepare_crt_only(task_root, pin, base, opener=srv)
    assert again.value.code == "crt_declined"
    with pytest.raises(pl.PrepareError) as full:
        pl.prepare(task_root, pin, opener=srv, system_root=str(sysroot))
    assert full.value.code == "crt_declined"
    assert not (base / "msvcp140.dll").exists() and not (task_root / "crt" / CRT_V).exists()
    assert not (task_root / "llama" / "b0-cpu-x64").exists()


def test_explicit_setup_rerun_offers_current_terms_again_after_a_decline_but_never_accepts(tmp_path, task_root, python_base):
    base, _ = python_base
    pin, pkg = write_crt_pin(tmp_path)
    srv = crt_server(pkg)
    with pytest.raises(pl.PrepareError):
        pl.prepare_crt_only(task_root, pin, base, opener=srv, decline=True)
    consent = pl.consent_path(task_root, pl.load_crt_section(pin))
    declined = consent.read_bytes()
    with pytest.raises(pl.PrepareError) as plain:      # launch/readiness path: the decline stands, no prompt
        pl.prepare_crt_only(task_root, pin, base, opener=srv)
    assert plain.value.code == "crt_declined"
    with pytest.raises(pl.PrepareError) as again:      # explicit setup rerun: terms offered, nothing accepted
        pl.prepare_crt_only(task_root, pin, base, opener=srv, reconsider=True)
    d = again.value.detail
    assert again.value.code == "crt_terms_pending" and d["previous_decision"] == "declined" and d["eula_sha256"] == sha(CRT_LICENSE)
    assert consent.read_bytes() == declined and not (task_root / "crt" / pl.PYTHON_STAGE).exists()
    with pytest.raises(pl.PrepareError) as no:         # answering anything but y keeps the decline
        pl.prepare_crt_only(task_root, pin, base, opener=srv, decline=True)
    assert no.value.code == "crt_declined" and json.loads(consent.read_text("utf-8"))["decision"] == "declined"
    out = pl.prepare_crt_only(task_root, pin, base, opener=srv, accept_terms_sha=d["eula_sha256"])   # explicit y
    assert out["python"] == "staged" and json.loads(consent.read_text("utf-8"))["decision"] == "accepted"
    code = pl.main(["--root", str(task_root), "--pin", str(pin), "--crt-only", "--python-base", str(base), "--reconsider-crt-terms"])
    assert code == 0  # once accepted, a reconsider run neither re-prompts nor changes the decision


def test_tampered_package_is_rejected_before_parsing(tmp_path, task_root, python_base):
    base, _ = python_base
    pin, pkg = write_crt_pin(tmp_path)
    bad = bytearray(pkg)
    bad[-1] ^= 1
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, base, opener=crt_server(bytes(bad)))
    assert e.value.code == "download_integrity"
    assert not (task_root / "crt").exists() or not any((task_root / "crt").glob("terms-*"))


@pytest.mark.parametrize("mutate,code", [
    (lambda c: c["burn"]["attached"].update(offset=c["burn"]["attached"]["offset"] + 4), "archive_unsafe"),
    (lambda c: c["burn"]["ux"].update(sha256="0" * 64), "archive_unsafe"),
    (lambda c: c["cab"].update(sha256="0" * 64), "download_integrity"),
    (lambda c: c["files"][0].update(sha256="0" * 64), "download_integrity"),
], ids=["container-offset", "ux-container-sha", "runtime-cab-sha", "dll-sha"])
def test_every_layer_is_bound_to_the_pin(tmp_path, task_root, python_base, mutate, code):
    base, _ = python_base
    pin, pkg = write_crt_pin(tmp_path, crt_override=mutate)
    srv = crt_server(pkg)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, base, opener=srv, accept_terms_sha=sha(CRT_LICENSE))
    assert e.value.code == code
    assert not (base / "msvcp140.dll").exists() and not (task_root / "crt" / CRT_V).exists()


def test_cab_reader_refuses_unsafe_names_lzx_and_oversize_blocks():
    import ms_vcredist as ms
    with pytest.raises(ms.PackageError):
        ms.cab_entries(make_cab([("x", b"1")], unsafe_name="..\\evil.dll"), {"x"})
    lzx = bytearray(make_cab([("x", b"1")]))
    struct.pack_into("<H", lzx, 36 + 6, 3)  # folder typeCompress = LZX
    with pytest.raises(ms.PackageError):
        ms.cab_entries(bytes(lzx), {"x"})
    big = bytearray(make_cab([("x", b"1" * 10)], comp=0))
    struct.pack_into("<H", big, 36 + 8 + 16 + 2 + 4, 60000)  # first CFDATA cbData beyond bound
    with pytest.raises(ms.PackageError):
        ms.cab_entries(bytes(big), {"x"})
    with pytest.raises(ms.PackageError):
        ms.cab_entries(make_cab([("x", b"1"), ("X", b"2")]), {"x"})  # case-duplicate


@pytest.mark.parametrize("mutate", [
    lambda c: c.update(source="ryoshu_zip"),
    lambda c: c["package"].update(url="https://example.invalid/VC_redist.x64.exe"),
    lambda c: c["package"].update(download_hosts=["download.visualstudio.microsoft.com", "evil.example"]),
    lambda c: c.update(files=[f for f in c["files"] if f["name"] != "msvcp140_1.dll"]),
    lambda c: c["files"][0].update(file_version="14.51.36247.0"),
], ids=["not-direct", "non-microsoft-url", "extra-host", "incomplete-set", "mixed-versions"])
def test_crt_section_must_be_the_official_microsoft_package(tmp_path, task_root, python_base, mutate):
    base, _ = python_base
    pin, pkg = write_crt_pin(tmp_path, crt_override=mutate)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, base, opener=crt_server(pkg))
    assert e.value.code == "pin_invalid"


def test_never_downgrades_a_newer_interpreter_vcruntime(tmp_path, task_root, python_base):
    base, versions = python_base
    versions["vcruntime140.dll"] = "14.99.0.0"
    pin, pkg = write_crt_pin(tmp_path)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, base, opener=crt_server(pkg), accept_terms_sha=sha(CRT_LICENSE))
    assert e.value.code == "crt_version_mismatch"
    assert not (task_root / "crt" / pl.PYTHON_STAGE).exists() and not (base / "msvcp140.dll").exists()


def test_older_interpreter_vcruntime_is_staged_for_update_and_current_is_left_alone(tmp_path, task_root, python_base):
    base, versions = python_base
    versions["vcruntime140.dll"] = versions["vcruntime140_1.dll"] = "14.44.35211.0"
    pin, pkg = write_crt_pin(tmp_path)
    out = pl.prepare_crt_only(task_root, pin, base, opener=crt_server(pkg), accept_terms_sha=sha(CRT_LICENSE))
    assert out["python"] == "staged" and out["vcruntime140.dll"] == "staged"
    import shutil as _sh
    _sh.rmtree(task_root / "crt" / pl.PYTHON_STAGE)
    for n in pl.APP_CRT:  # as if setup had applied the stage
        (base / n).write_bytes(CRT_FILES[n])
    again = pl.prepare_crt_only(task_root, pin, base, opener=crt_server(pkg))
    assert again["python"] == "current" and not (task_root / "crt" / pl.PYTHON_STAGE).exists()


def test_official_terms_document_is_fetched_from_microsoft_and_bound_to_consent(tmp_path, task_root, python_base):
    import ms_vcredist as ms
    base, _ = python_base
    docx = make_docx(["MICROSOFT SOFTWARE LICENSE TERMS", "MICROSOFT VISUAL C++ V14 REDISTRIBUTABLE and RUNTIME", "EULA ID: Cpp_v14_fixture"])
    durl = "https://visualstudio.microsoft.com/wp-content/uploads/2025/10/terms.docx"
    pin, pkg = write_crt_pin(tmp_path, crt_override=lambda c: c.update(license={
        "document_url": durl, "download_hosts": ["visualstudio.microsoft.com"], "bytes": len(docx), "sha256": sha(docx),
        "format": "docx", "eula_id": "Cpp_v14_fixture", "title": "fixture v14"}))
    srv = Server({MS_URL: pkg, durl: docx})
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, base, opener=srv)
    d = e.value.detail
    assert e.value.code == "crt_terms_pending" and d["eula_sha256"] == sha(docx) and d["terms_url"] == durl
    assert "V14 REDISTRIBUTABLE" in Path(d["terms_text"]).read_text("utf-8") and Path(d["terms_document"]).read_bytes() == docx
    with pytest.raises(pl.PrepareError) as wrong:
        pl.prepare_crt_only(task_root, pin, base, opener=srv, accept_terms_sha=sha(CRT_LICENSE))  # not the shown terms
    assert wrong.value.code == "crt_terms_mismatch"
    out = pl.prepare_crt_only(task_root, pin, base, opener=srv, accept_terms_sha=sha(docx))
    rec = json.loads(pl.consent_path(task_root, pl.load_crt_section(pin)).read_text("utf-8"))
    assert out["python"] == "staged" and rec["eula_sha256"] == sha(docx) and rec["eula_id"] == "Cpp_v14_fixture"
    assert sorted(p.name for p in (task_root / "crt" / CRT_V).iterdir()) == sorted([*CRT_FILES, pl.LICENSE_DOCX])
    with pytest.raises(ms.PackageError):
        ms.docx_to_text(make_docx(["x"], doctype=True))


@pytest.mark.parametrize("mutate", [
    lambda l: l.update(document_url="https://example.com/terms.docx"),
    lambda l: l.update(download_hosts=["visualstudio.microsoft.com", "evil.example"]),
    lambda l: l.update(format="html"),
], ids=["non-microsoft-url", "extra-host", "not-docx"])
def test_terms_document_must_be_microsofts_own(tmp_path, task_root, python_base, mutate):
    base, _ = python_base
    lic = {"document_url": "https://visualstudio.microsoft.com/t.docx", "download_hosts": ["visualstudio.microsoft.com"],
           "bytes": 10, "sha256": "a" * 64, "format": "docx", "eula_id": "x"}
    mutate(lic)
    pin, pkg = write_crt_pin(tmp_path, crt_override=lambda c: c.update(license=lic))
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, base, opener=crt_server(pkg))
    assert e.value.code == "pin_invalid"


def test_adopted_crt_requires_runtime_to_carry_its_crt_members(tmp_path, task_root, sysroot, python_base):
    base, _ = python_base
    pin, pkg = write_crt_pin(tmp_path, runtime_crt=())
    pl.prepare_crt_only(task_root, pin, base, opener=crt_server(pkg), accept_terms_sha=sha(CRT_LICENSE))
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(task_root, pin, opener=crt_server(pkg), system_root=str(sysroot))
    assert e.value.code == "pin_invalid"


def test_without_crt_section_closure_is_reported_and_missing_fails_closed(tmp_path, task_root, python_base, sysroot):
    base, _ = python_base
    pin = write_pin(tmp_path)
    ok = pl.prepare_crt_only(task_root, pin, base, opener=serve(), system_root=str(sysroot))
    assert ok["crt"] == "system_or_python_local_unmanaged"
    assert ok["closure"] == {"msvcp140.dll": "system32", "msvcp140_1.dll": "system32",
                             "vcruntime140.dll": "python_local", "vcruntime140_1.dll": "python_local"}
    empty = tmp_path / "stock"
    (empty / "System32").mkdir(parents=True)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, base, opener=serve(), system_root=str(empty))
    assert e.value.code == "crt_unavailable" and "msvcp140.dll" in e.value.detail and "msvcp140_1.dll" in e.value.detail
    assert not (base / "msvcp140.dll").exists()


def test_foreign_msvcp_in_python_base_is_untouched_by_helper_and_recorded_for_backup(tmp_path, task_root, python_base):
    base, _ = python_base
    (base / "msvcp140.dll").write_bytes(b"MZ some other version")
    pin, pkg = write_crt_pin(tmp_path)
    pl.prepare_crt_only(task_root, pin, base, opener=crt_server(pkg), accept_terms_sha=sha(CRT_LICENSE))
    assert (base / "msvcp140.dll").read_bytes() == b"MZ some other version"
    plan = json.loads((task_root / "crt" / pl.PYTHON_STAGE / "plan.json").read_text("utf-8"))
    assert plan["files"]["msvcp140.dll"]["current_sha256"] == sha(b"MZ some other version")


def test_main_reports_terms_identity_for_setup_and_accepts_explicit_choice(tmp_path, task_root, python_base, capsys, monkeypatch):
    base, _ = python_base
    pin, pkg = write_crt_pin(tmp_path)
    # no network in tests: seed the verified package (re-hashed by the preparer) and make any real request fail loudly
    (task_root / "downloads").mkdir(parents=True)
    (task_root / "downloads" / f"{sha(pkg)}.bin").write_bytes(pkg)
    monkeypatch.setattr(pl.urllib.request, "build_opener", lambda *a, **k: pytest.fail("network attempted"))
    code = pl.main(["--root", str(task_root), "--pin", str(pin), "--crt-only", "--python-base", str(base)])
    assert code == 3
    res = json.loads((task_root / "runtime/crt-setup-result.json").read_text("utf-8"))
    assert res["reason_code"] == "crt_terms_pending" and res["eula_sha256"] == sha(CRT_LICENSE)
    assert Path(res["terms_text"]).is_file() and "closure" in res
    code = pl.main(["--root", str(task_root), "--pin", str(pin), "--crt-only", "--python-base", str(base),
                    "--accept-crt-terms", res["eula_sha256"]])
    res = json.loads((task_root / "runtime/crt-setup-result.json").read_text("utf-8"))
    assert code == 0 and res["python"] == "staged" and Path(res["stage"]) == task_root / "crt" / pl.PYTHON_STAGE


MAT = ROOT.parent / "harness-opus/final-crt/material"


@pytest.mark.skipif(not (MAT / "VC_redist.x64-14.51.36247.0.exe").is_file(), reason="private verified cache absent")
def test_real_microsoft_package_and_terms_match_the_pin():
    import ms_vcredist as ms
    pkg = (MAT / "VC_redist.x64-14.51.36247.0.exe").read_bytes()
    docx = (MAT / "terms-docx/license.docx").read_bytes()
    crt = pl.load_crt_section(ROOT / "receipt_ledger/runtime_pin.json")
    spec = pl._ms_spec(crt)
    assert sha(docx) == crt["license"]["sha256"] and len(docx) == crt["license"]["bytes"]
    text = ms.docx_to_text(docx)
    assert "MICROSOFT VISUAL C++ V14 REDISTRIBUTABLE and RUNTIME" in text and "EULA ID: Cpp_v14_ENU.1033" in text
    got = ms.read_runtime_files(pkg, spec)
    assert {n: sha(b) for n, b in got.items()} == {n: f["sha256"] for n, f in crt["files"].items()}


def test_main_accepts_relative_root_and_never_tracebacks(tmp_path, monkeypatch, capsys):
    root = tmp_path / "rel root 日本語"
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pl, "vc_runtime_present", lambda *a: ["msvcp140.dll"])
    code = pl.main(["--root", "rel root 日本語", "--pin", str(write_pin(tmp_path))])
    assert code == 3 and json.loads(capsys.readouterr().out)["reason_code"] == "vc_runtime_missing"
    assert json.loads((root / "runtime/llama-setup-result.json").read_text("utf-8"))["reason_code"] == "vc_runtime_missing"


# --- linked roots / ancestors / result directory (never written through) ---------------------------
def snapshot(folder):
    return {p.relative_to(folder).as_posix(): (p.read_bytes() if p.is_file() else None) for p in sorted(folder.rglob("*"))}


@pytest.fixture
def outside(tmp_path):
    """A NEW private target with a sentinel; links created in a test point here. Links are removed with
    os.rmdir/os.unlink (link only) before tmp cleanup, so cleanup never follows into the target."""
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
                pytest.skip("symlink creation needs a privilege not held by this test process")
        links.append(at)
        assert runtime_pin.is_link(at)
        return at

    before = snapshot(target)
    yield target, link, before
    for at in reversed(links):
        if runtime_pin.is_link(at):
            os.rmdir(at)  # removes the link itself, never the target's contents
    assert snapshot(target) == before  # cleanup itself never touched the target


pytestmark_win = pytest.mark.skipif(os.name != "nt", reason="Windows reparse points")


@pytestmark_win
@pytest.mark.parametrize("kind", ["junction", "symlink"])
def test_linked_input_root_is_refused_before_any_write_or_download(tmp_path, sysroot, outside, kind):
    target, link, before = outside
    root = link(tmp_path / f"root {kind} 日本語", kind)
    pin = write_pin(tmp_path)
    srv = serve()
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(root, pin, opener=srv, system_root=str(sysroot))
    assert e.value.code == "link_refused" and srv.calls == []
    assert snapshot(target) == before


@pytestmark_win
def test_relative_path_to_linked_root_is_refused(tmp_path, sysroot, outside, monkeypatch):
    target, link, before = outside
    link(tmp_path / "rel linked root")
    monkeypatch.chdir(tmp_path)
    srv = serve()
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(Path("rel linked root"), write_pin(tmp_path), opener=srv, system_root=str(sysroot))
    assert e.value.code == "link_refused" and srv.calls == []
    assert snapshot(target) == before


@pytestmark_win
def test_linked_ancestor_of_root_is_refused(tmp_path, sysroot, outside):
    target, link, before = outside
    ancestor = link(tmp_path / "profile link 日本語")
    root = ancestor / "StudioYebisu" / "ReceiptLedger"  # does not exist; must not be created inside the target
    srv = serve()
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare(root, write_pin(tmp_path), opener=srv, system_root=str(sysroot))
    assert e.value.code == "link_refused" and srv.calls == []
    assert snapshot(target) == before


@pytestmark_win
def test_crt_only_with_linked_root_or_python_base_is_refused(tmp_path, outside, python_base):
    target, link, before = outside
    base, _ = python_base
    pin, crt_bytes = write_crt_pin(tmp_path)
    srv = crt_server(crt_bytes)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(link(tmp_path / "crt root link"), pin, base, opener=srv)
    assert e.value.code == "link_refused"
    real_root = tmp_path / "real crt root"
    real_root.mkdir()
    with pytest.raises(pl.PrepareError) as e2:
        pl.prepare_crt_only(real_root, pin, link(real_root / "python" / "base link"), opener=srv)
    assert e2.value.code == "link_refused"
    assert srv.calls == [] and snapshot(target) == before
    assert not (real_root / "crt").exists()


@pytest.mark.parametrize("where", ["sibling", "root itself", "python home itself", "dotdot escape", "other drive"])
def test_crt_only_refuses_an_interpreter_not_managed_under_root(tmp_path, task_root, python_base, where):
    base, _ = python_base
    shared = {"sibling": tmp_path / "shared python", "root itself": task_root, "python home itself": task_root / "python",
              "dotdot escape": Path(str(task_root / "python" / ".." / ".." / "elsewhere")),
              "other drive": Path("Z:/Python312" if os.name == "nt" else "/opt/python")}[where]
    pin, pkg = write_crt_pin(tmp_path)
    srv = crt_server(pkg)
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, shared, opener=srv, accept_terms_sha=sha(CRT_LICENSE))
    assert e.value.code == "python_not_owned"
    assert srv.calls == [] and not (task_root / "crt").exists() and not (task_root / "downloads").exists()
    with pytest.raises(pl.PrepareError) as e2:
        pl.install_python_crt(pl.load_crt_section(pin), task_root, shared, sha(pin.read_bytes()))
    assert e2.value.code == "python_not_owned"


def test_legacy_model_license_record_is_never_microsoft_consent(tmp_path, task_root, python_base):
    base, _ = python_base
    (task_root / "model-license-acceptance.json").write_bytes(b"")
    pin, pkg = write_crt_pin(tmp_path)
    crt = pl.load_crt_section(pin)
    assert pl.consent_state(task_root, crt) == "none"
    (task_root / "crt").mkdir()
    for junk in (b"", b"{}", json.dumps({"decision": "accepted"}).encode(),
                 json.dumps({"license": "Indra modified OpenRAIL-M", "accepted_at": "x"}).encode()):
        pl.consent_path(task_root, crt).write_bytes(junk)
        assert pl.consent_state(task_root, crt) == "none"
    pl.consent_path(task_root, crt).unlink()
    with pytest.raises(pl.PrepareError) as e:
        pl.prepare_crt_only(task_root, pin, base, opener=crt_server(pkg))
    assert e.value.code == "crt_terms_pending" and not (base / "msvcp140.dll").exists()


@pytestmark_win
@pytest.mark.parametrize("crt_only", [False, True])
def test_main_with_linked_root_reports_code_and_writes_nothing_there(tmp_path, outside, capsys, crt_only):
    target, link, before = outside
    root = link(tmp_path / "main root link 日本語")
    args = ["--root", str(root), "--pin", str(write_pin(tmp_path))]
    if crt_only:
        args += ["--crt-only", "--python-base", str(tmp_path)]
    code = pl.main(args)
    assert code == 3
    assert json.loads(capsys.readouterr().out) == {"ok": False, "reason_code": "link_refused"}
    assert snapshot(target) == before  # no runtime\ directory, no result file through the link


@pytestmark_win
def test_main_failure_never_writes_result_through_linked_runtime_dir(tmp_path, outside, capsys, monkeypatch):
    target, link, before = outside
    root = tmp_path / "real root 日本語 space"
    root.mkdir()
    link(root / "runtime")
    monkeypatch.setattr(pl, "vc_runtime_present", lambda *a: ["msvcp140.dll"])
    code = pl.main(["--root", str(root), "--pin", str(write_pin(tmp_path))])
    assert code == 3 and json.loads(capsys.readouterr().out)["reason_code"] == "vc_runtime_missing"
    assert snapshot(target) == before  # failure report did not land in the linked runtime\ target


# --- the real product pin --------------------------------------------------------------------------
def test_product_pin_is_complete_verified_and_placeholder_free():
    path = ROOT / "receipt_ledger/runtime_pin.json"
    pin = runtime_pin.load_pin(path)
    p, extra = pl.load_install_pin(path)
    raw = json.loads(path.read_text("utf-8"))
    assert "<" not in path.read_text("utf-8")
    names = {m.name for m in pin.members}
    assert len(pin.members) == 27 and pin.server_exe == "llama-server.exe"
    assert {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"} <= names  # app-local CRT, full-hashed by backend
    crt = pl.load_crt_section(path)
    # current supported v14 (winget microsoft/winget-pkgs blob 876bdbf0, x64 entry only)
    assert crt["version"] == "14.51.36247.0"
    assert crt["package"]["sha256"] == "843068991daaa1f73ad9f6239bce4d0f6a07a51f18c37ea2a867e9beca71295c"
    assert crt["package"]["bytes"] == 18731856 and crt["package"]["hosts"] == ["download.visualstudio.microsoft.com"]
    assert crt["package"]["url"].startswith("https://download.visualstudio.microsoft.com/")
    assert crt["license"]["sha256"] == "08651651a7602fc7c0e2763de0fde1ff9f868df2780597cd1775ee9d6441c783"
    assert crt["license"]["eula_id"] == "Cpp_v14_ENU.1033" and crt["license"]["hosts"] == ["visualstudio.microsoft.com"]
    assert {n: f["sha256"][:16] for n, f in crt["files"].items()} == {
        "msvcp140.dll": "7c26614e1d733892", "msvcp140_1.dll": "206c931bf90fdad8",
        "vcruntime140.dll": "d1f4225df2cd877d", "vcruntime140_1.dll": "a7146c08f89fe5b0"}
    for m in pin.members:
        if m.name in crt["files"]:
            assert m.sha256 == crt["files"][m.name]["sha256"]  # llama runtime CRT == the same current files
    assert ".invalid" not in path.read_text("utf-8") and "ryoshu" not in crt["package"]["url"].lower()
    assert {"llama-server-impl.dll", "ggml-cpu-x64.dll", "ggml-cpu-sse42.dll", "ggml-cpu-zen4.dll", "libomp.dll",
            "LICENSE-LLVM-OpenMP", "LICENSE-llama.cpp.txt"} <= names
    assert len([n for n in names if n.startswith("ggml-cpu-")]) == 14
    assert not {"ggml-rpc.dll", "ggml-rpc-server.exe"} & names  # RPC backend is never installed
    lic = next(m for m in pin.members if m.name == "LICENSE-llama.cpp.txt")
    assert lic.sha256 == sha(MIT) and b"Copyright (c) 2023-2026 The ggml authors" in MIT
    assert raw["runtime"]["asset_sha256"] == "4e2af6b2b39691cd84267a02839596ed6653ea2dde5ee9cdbfeb4dd9677eecbb"
    assert raw["runtime"]["asset_bytes"] == 19593035
    assert pin.model_sha256 == "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"
    assert pin.model_bytes == 2497280256 and pin.model_revision == "bc640142c66e1fdd12af0bd68f40445458f3869b"
    assert pin.chat_template_sha256 == "57f1fd00f0013a2be96aa79b857391f27e23df5b5f847072b524c897e24d0361"
    assert extra["model_license_sha256"] == sha(QWEN) == "5de36594c10839788a8c589443a8ef9d8b8d17c65a1b5807206ae037fc36c6bd"
    assert extra["runtime_hosts"] == ["github.com", "release-assets.githubusercontent.com"]
    assert extra["model_hosts"] == ["huggingface.co", "us.aws.cdn.hf.co"]


def test_public_source_tree_contains_no_runtime_binaries_or_weights():
    banned = (".exe", ".dll", ".gguf", ".part", ".safetensors", ".onnx")
    skip = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", "_design-staging-llamacpp"}
    found = [p for p in ROOT.rglob("*") if p.is_file() and p.suffix.lower() in banned and not skip & set(p.relative_to(ROOT).parts)]
    assert found == []
