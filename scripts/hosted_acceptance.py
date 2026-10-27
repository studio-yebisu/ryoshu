"""Hosted Windows acceptance of the ACTUAL built per-user installer on a disposable runner profile.

Flow: runner facts -> Ollama absent -> install the built EXE into a fresh profile (spaces + Japanese) -> payload ==
source ZIP == build report -> real first setup (official pinned Python/deps/Indra/llama.cpp/GGUF + Microsoft VC++
directly from Microsoft; synthetic QA terms flag, recorded as 'qa_test_flag') -> two fictional receipts through real
OCR + local CPU llama.cpp -> CSV -> start.ps1 -NoBrowser health -> stop -> uninstall keeps data -> Ollama still absent.

This is CI acceptance, NOT the 100-receipt benchmark; it records no accuracy/speed statistics for that benchmark.
A hosted runner image is NOT a stock consumer Windows 11 (it ships developer tools and may have a system VC++
runtime); the report records that distinction and proves where the inference CRTs actually mapped from.
Only acceptance.json (sanitized) and the synthetic CSV are written to --report-dir. Everything else stays private.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_windows_package import registry_exists, sha, tree_sha  # noqa: E402  (same HKCU refusal + hashing)

CRT_NAMES = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll")
LLAMA_CRT = ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")
UPLOAD_ALLOWLIST = ("acceptance.json", "synthetic-expenses.csv")
SECRET_KEYS = re.compile(r"(secret|token|password|hmac|session|auth|key)$", re.I)


# ----------------------------------------------------------------------------------------------- pure checks
def ollama_findings(probe: dict) -> list[str]:
    """Probe facts -> list of Ollama traces (empty = absent)."""
    found = []
    if probe.get("command"):
        found.append("command_on_path")
    found += [f"install_dir:{d}" for d in probe.get("install_dirs", [])]
    found += [f"service:{s}" for s in probe.get("services", [])]
    found += [f"process:{p}" for p in probe.get("processes", [])]
    if probe.get("listener_11434"):
        found.append("listener_11434")
    return found


def crt_provenance(modules: list[dict], pin_files: dict, allowed_dir: str, required: tuple, system_root: str) -> dict:
    """Every VC++ redistributable module must map from allowed_dir at a pinned hash (vendored, name-mangled wheel
    copies are reported separately). Anything from %SystemRoot% or elsewhere fails."""
    allowed = os.path.normcase(os.path.join(allowed_dir, ""))
    sysroot = os.path.normcase(os.path.join(system_root, ""))
    pinned = {n: f["sha256"] for n, f in pin_files.items()}
    owned, vendored, failures = {}, [], []
    for m in modules:
        if m.get("class") != "redistributable_crt":
            continue
        path, name = os.path.normcase(m["path"]), m["name"].lower()
        if name in pinned:
            if not path.startswith(allowed):
                failures.append(f"{name}:outside_managed_dir" + (":system_root" if path.startswith(sysroot) else ""))
            elif m.get("sha256") != pinned[name]:
                failures.append(f"{name}:hash_not_pinned")
            else:
                owned[name] = m["sha256"]
        elif re.fullmatch(r"(msvcp|vcruntime|concrt|vcomp)\d+[0-9a-z_]*-[0-9a-f]{8,64}\.dll", name) and "site-packages" in path:
            vendored.append({"name": name, "sha256": m.get("sha256")})
        else:
            failures.append(f"{name}:unexpected_redistributable" + (":system_root" if path.startswith(sysroot) else ""))
    failures += [f"{n}:not_loaded_from_managed_dir" for n in required if n not in owned]
    return {"ok": not failures, "owned": owned, "vendored_wheel_copies": vendored, "failures": failures}


def sanitize(value, replacements: dict[str, str]):
    """Replace private absolute prefixes with labels and drop secret-like keys, recursively."""
    if isinstance(value, dict):
        return {k: sanitize(v, replacements) for k, v in value.items() if not SECRET_KEYS.search(str(k))}
    if isinstance(value, list):
        return [sanitize(v, replacements) for v in value]
    if isinstance(value, str):
        for prefix, label in sorted(replacements.items(), key=lambda kv: -len(kv[0])):
            if prefix:
                value = re.sub(re.escape(prefix), label.replace("\\", "\\\\"), value, flags=re.I)
        return value
    return value


def known_programs_dir() -> Path:
    """Current user's Start Menu Programs folder via SHGetKnownFolderPath(FOLDERID_Programs) - the Shell's own
    answer, independent of the USERPROFILE/APPDATA environment variables of this process."""
    import ctypes
    from ctypes import wintypes
    import uuid as _uuid

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    g = _uuid.UUID("{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}")  # FOLDERID_Programs
    fid = GUID(g.fields[0], g.fields[1], g.fields[2], (ctypes.c_ubyte * 8)(*g.bytes[8:]))
    shell32, ole32 = ctypes.WinDLL("shell32"), ctypes.WinDLL("ole32")
    shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
    shell32.SHGetKnownFolderPath.restype = ctypes.HRESULT
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    out = ctypes.c_wchar_p()
    KF_FLAG_DONT_VERIFY = 0x00004000
    shell32.SHGetKnownFolderPath(ctypes.byref(fid), KF_FLAG_DONT_VERIFY, None, ctypes.byref(out))  # raises OSError on failure
    try:
        return Path(out.value)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))


def start_menu_group(query=known_programs_dir) -> Path:
    return query() / "Ryoshu"


def payload_from_zip(source_zip: Path) -> dict[str, str]:
    with zipfile.ZipFile(source_zip) as z:
        return {i.filename.split("/", 1)[1]: hashlib.sha256(z.read(i)).hexdigest() for i in z.infolist() if not i.is_dir()}


# ----------------------------------------------------------------------------------------------- runner probes
def ps(command: str, env=None, timeout=120) -> str:
    r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env, timeout=timeout)
    return r.stdout.strip()


def probe_ollama() -> dict:
    local = os.environ.get("LOCALAPPDATA", "")
    dirs = [d for d in (os.path.join(local, "Programs", "Ollama"), os.path.join(os.environ.get("ProgramFiles", ""), "Ollama"))
            if d and os.path.exists(d)]
    services = [s for s in ps("Get-Service -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*ollama*' } | "
                               "ForEach-Object { $_.Name }").splitlines() if s]
    processes = [p for p in ps("Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*ollama*' } | "
                                "ForEach-Object { $_.Name }").splitlines() if p]
    with socket.socket() as s:
        s.settimeout(1)
        listener = s.connect_ex(("127.0.0.1", 11434)) == 0
    return {"command": bool(shutil.which("ollama")), "install_dirs": dirs, "services": services, "processes": processes,
            "listener_11434": listener}


def vc_redist_registrations() -> list[str]:
    """Machine + per-user uninstall entries for Visual C++ runtimes (to prove setup adds none)."""
    roots = ["HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
             "HKLM:\\Software\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall",
             "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall"]
    out = ps("@(" + ",".join(f"'{r}'" for r in roots) + ") | ForEach-Object { Get-ChildItem $_ -ErrorAction SilentlyContinue } | "
             "ForEach-Object { $_.GetValue('DisplayName') } | Where-Object { $_ -like '*Visual C++*' }")
    return sorted(line for line in out.splitlines() if line)


def runner_facts() -> dict:
    osinfo = ps("$o = Get-CimInstance Win32_OperatingSystem; [ordered]@{caption=$o.Caption; version=$o.Version; "
                "build=$o.BuildNumber; product_type=$o.ProductType} | ConvertTo-Json -Compress")
    sys32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return {"kind": "github_hosted_runner" if os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted" else "other",
            "is_stock_consumer_windows11": False,
            "note": "hosted image ships developer tooling and may already contain a system VC++ runtime; not a bare consumer Windows 11",
            "image_os": os.environ.get("ImageOS"), "image_version": os.environ.get("ImageVersion"),
            "runner_os": os.environ.get("RUNNER_OS"), "runner_arch": os.environ.get("RUNNER_ARCH"),
            "os": json.loads(osinfo) if osinfo.startswith("{") else None, "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "system32_vc_redist_present": {n: (sys32 / n).is_file() for n in CRT_NAMES}}


# ----------------------------------------------------------------------------------------------- main flow
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--installer", type=Path, required=True)
    ap.add_argument("--build-report", type=Path, required=True)
    ap.add_argument("--source-zip", type=Path, required=True)
    ap.add_argument("--fixtures", type=Path, required=True, help="samples/receipts-100-v2/receipts-100.zip")
    ap.add_argument("--work-dir", type=Path, required=True, help="new empty private folder (never uploaded)")
    ap.add_argument("--report-dir", type=Path, required=True, help="new empty folder; receives ONLY the allowlisted files")
    a = ap.parse_args(argv)
    if sys.platform != "win32":
        ap.error("Windows only")
    if os.environ.get("GITHUB_ACTIONS") != "true" and os.environ.get("RYOSHU_ALLOW_LOCAL_ACCEPTANCE") != "1":
        ap.error("Disposable hosted runner only (it installs, runs first setup and uninstalls)")
    if registry_exists():
        ap.error("An installed Ryoshu already exists; do not replace it for QA")
    for d in (a.work_dir, a.report_dir):
        if d.exists() and any(d.iterdir()):
            ap.error(f"Choose a new empty folder: {d.name}")
        d.mkdir(parents=True, exist_ok=True)
    work = a.work_dir.resolve()
    profile = work / "QA 領収書 profile"
    install = profile / "AppData/Local/Programs/StudioYebisu/Ryoshu"
    root = profile / "StudioYebisu/ReceiptLedger"
    # Inno's {group} resolves through the Shell Known Folder (FOLDERID_Programs) of the CURRENT user, which a changed
    # USERPROFILE does not redirect. Query it; never derive it from the fake profile and never change the mapping.
    group_dir = start_menu_group()
    if group_dir.exists():
        ap.error("A Start Menu 'Ryoshu' group already exists for this user; refusing to overwrite it")
    env = {**os.environ, "USERPROFILE": str(profile), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    for k in [k for k in env if k.upper() in ("RECEIPT_LEDGER_RUNTIME_ROOT", "RECEIPT_LEDGER_DATA", "HF_HOME")]:
        env.pop(k)
    report = {"schema": "ryoshu.hosted-windows-acceptance/1", "status": "failed", "stage": "start",
              "not_benchmark": "CI acceptance only; never merged into the 100-receipt benchmark statistics",
              "runner": runner_facts(), "checks": []}
    replacements = {str(install): "<install>", str(root): "<root>", str(profile): "<profile>", str(work): "<work>",
                    os.environ.get("SystemRoot", r"C:\Windows"): "<SystemRoot>", str(Path(__file__).resolve().parents[1]): "<checkout>"}

    def check(name, ok, **detail):
        report["checks"].append({"check": name, "ok": bool(ok), **detail})
        if not ok:
            raise AssertionError(name)

    installed = False
    try:
        report["stage"] = "preflight"
        before = probe_ollama()
        check("ollama_absent_before", not ollama_findings(before), findings=ollama_findings(before))
        build = json.loads(a.build_report.read_text(encoding="utf-8"))
        payload = payload_from_zip(a.source_zip)
        check("build_report_binds_installer_and_source", build["installer_sha256"] == sha(a.installer)
              and build["source_sha256"] == sha(a.source_zip) and build["payload_files"] == payload,
              installer_sha256=sha(a.installer), source_zip_sha256=sha(a.source_zip), payload_files=len(payload))
        report["stage"] = "install"
        profile.mkdir(parents=True)
        subprocess.run([str(a.installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=" + str(install),
                        "/TASKS=", "/LOG=" + str(work / "install.log")], check=True, env=env, timeout=600)
        installed = True
        check("installer_registered_per_user", registry_exists())
        check("installed_payload_equals_source_zip_and_build_report",
              {n: sha(install / n) for n in payload} == payload == build["payload_files"], files=len(payload))
        check("start_menu_shortcuts", {p.name for p in group_dir.glob("*.lnk")} ==
              {"Ryoshu.lnk", "Ryoshuを終了.lnk", "Ryoshuの導入ガイド.lnk"})
        pin = json.loads((install / "receipt_ledger/runtime_pin.json").read_text(encoding="utf-8"))
        pin_sha = sha(install / "receipt_ledger/runtime_pin.json")
        crt = pin["crt"]
        report["source_pin"] = {"sha256": pin_sha, "crt_version": crt["version"], "model_sha256": pin["model"]["sha256"],
                                "runtime_tag": pin["runtime"]["tag"], "terms_sha256": crt["license"]["sha256"],
                                "package_sha256": crt["package"]["sha256"]}

        report["stage"] = "first_setup"
        vc_before = vc_redist_registrations()
        report["runner"]["vc_redist_registrations_on_image"] = vc_before
        t = time.perf_counter()
        setup = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                                str(install / "setup.ps1"), "-AcceptModelLicense", "-QaAcceptCrtTermsSha256", crt["license"]["sha256"]],
                               env=env, cwd=install, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               stdin=subprocess.DEVNULL, timeout=5400)
        (work / "setup.log").write_text(setup.stdout + "\n" + setup.stderr, encoding="utf-8")   # private, never uploaded
        slots = sorted(set(re.findall(r"\[RYOSHU ([a-z_.]+)", setup.stdout + setup.stderr)))
        report["setup"] = {"exit_code": setup.returncode, "seconds": round(time.perf_counter() - t, 1), "slot_ids_seen": slots}
        check("first_setup_succeeded", setup.returncode == 0)
        state = json.loads((root / "runtime/setup-state.json").read_text(encoding="utf-8-sig"))
        check("setup_state_complete_with_inference", state["state"] == "complete" and state["inference_state"] == "complete"
              and state["runtime_pin_sha256"] == pin_sha)
        consent = json.loads((root / f"crt/consent-{crt['version']}.json").read_text(encoding="utf-8"))
        check("microsoft_consent_is_exact_and_marked_synthetic", consent["decision"] == "accepted" and consent["method"] == "qa_test_flag"
              and consent["eula_sha256"] == crt["license"]["sha256"] and consent["package_sha256"] == crt["package"]["sha256"]
              and consent["version"] == crt["version"])
        qa_event = json.loads((root / "runtime/qa-event-crt-terms.json").read_text(encoding="utf-8"))   # machine data, not copy
        check("qa_terms_event_matches_pin", qa_event == {"schema": 1, "event": "crt_terms_qa_test_acceptance", "method": "qa_test_flag",
                                                         "eula_sha256": crt["license"]["sha256"], "version": crt["version"]})
        base = next((root / "python").glob("cpython-3.12.15-*"))
        check("managed_python_crt_pinned", all(sha(base / n) == crt_f["sha256"] for n, crt_f in
                                               ((f["name"], f) for f in crt["files"])))
        check("no_global_vc_redist_installed_by_setup", vc_redist_registrations() == vc_before,
              preexisting_on_image=len(vc_before))

        report["stage"] = "receipts"
        private = work / "child.json"
        csv_out = a.report_dir / "synthetic-expenses.csv"
        cenv = {k: v for k, v in env.items() if k.upper() in ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS",
                                                             "PROCESSOR_ARCHITECTURE", "COMPUTERNAME", "USERPROFILE",
                                                             "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "PYTHONUTF8", "PYTHONIOENCODING")}
        cenv.update(PATH=os.path.join(os.environ["SystemRoot"], "System32") + ";" + os.environ["SystemRoot"],
                    RECEIPT_LEDGER_DATA=str(root / "qa-ledger/data"), HF_HUB_OFFLINE="1")
        child = subprocess.run([str(root / "venv/Scripts/python.exe"), "-I", "-B", str(Path(__file__).with_name("hosted_acceptance_child.py")),
                                "--app", str(install), "--fixtures", str(a.fixtures), "--out", str(private), "--csv", str(csv_out)],
                               env=cenv, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        (work / "child.log").write_text(child.stdout + "\n" + child.stderr, encoding="utf-8")
        check("receipt_child_succeeded", child.returncode == 0, exit_code=child.returncode)
        res = json.loads(private.read_text(encoding="utf-8"))
        check("strict_crt_activation_active", res["activation"]["state"] == "active")
        for r in res["receipts"]:
            check(f"real_ocr_{r['case_id']}", r["ocr_engine"] == "Indra OCR" and r["ocr_lines"] > 0 and r["ocr_text_chars"] > 0
                  and any(s not in ("text",) for s in r["ocr_page_sources"]))
            check(f"local_model_structured_output_{r['case_id']}", r["status"] == "review" and not r["error_present"]
                  and r["origin"] == "model" and r["raw_model_candidate_is_object"]
                  and r["model_digest"] == pin["model"]["sha256"] and r["runtime_pin_sha256"] == pin_sha)
        jpg = next(r for r in res["receipts"] if r["input"].endswith(".jpg"))
        check("image_input_has_no_native_pdf_text", jpg["native_pdf_text_chars"] == 0)
        check("csv_exported", res["csv"]["data_rows"] == len(res["receipts"]), **res["csv"])
        files = {f["name"]: f for f in crt["files"]}
        py = crt_provenance(res["python_modules"], files, str(base), CRT_NAMES, os.environ["SystemRoot"])
        check("python_crt_maps_from_managed_python", py["ok"], **py)
        llama_dir = root / "llama" / pin["runtime"]["dir_name"]
        ll = crt_provenance((res["llama_server"] or {}).get("modules", []), files, str(llama_dir), LLAMA_CRT, os.environ["SystemRoot"])
        check("llama_crt_maps_from_managed_runtime", res["llama_server"] is not None and ll["ok"], **ll)
        report["receipts"] = res["receipts"]
        report["cpu_args"] = res["cpu_args"]
        report["runtime_status"] = res["runtime_status"]
        report["module_counts"] = {"python": len(res["python_modules"]),
                                   "llama_server": len((res["llama_server"] or {}).get("modules", []))}

        report["stage"] = "launch_no_browser"
        start = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                                str(install / "start.ps1"), "-NoBrowser"], env=env, cwd=install, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=900)
        check("start_no_browser", start.returncode == 0, exit_code=start.returncode)
        with urlopen("http://127.0.0.1:8766/api/health", timeout=10) as r:
            health = json.load(r)
        check("health_is_installed_version", health.get("app") == "receipt-ledger" and health.get("app_version") == build["app_version"])
        stop = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                               str(install / "stop.ps1")], env=env, cwd=install, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL, timeout=300)
        check("stop_succeeded", stop.returncode == 0, exit_code=stop.returncode)

        report["stage"] = "uninstall"
        (root / "qa-ledger/data/SENTINEL-user-file.txt").write_text("fictional user file\n", encoding="utf-8")
        data_before = tree_sha(root / "qa-ledger")
        kept_dirs = {d: (root / d).is_dir() for d in ("python", "venv", "llama", "models", "crt")}
        subprocess.run([str(install / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                        "/LOG=" + str(work / "uninstall.log")], check=True, env=env, timeout=600)
        for _ in range(100):
            if not (install / "unins000.exe").exists():
                break
            time.sleep(0.2)
        installed = registry_exists()
        check("uninstall_removed_code_shortcuts_registration", not (install / "start.ps1").exists() and not group_dir.exists()
              and not registry_exists())
        check("uninstall_preserved_ledger_and_sentinel", tree_sha(root / "qa-ledger") == data_before, files=len(data_before))
        check("uninstall_kept_runtime_folders", all((root / d).is_dir() for d, was in kept_dirs.items() if was))
        after = probe_ollama()
        check("ollama_absent_after", not ollama_findings(after), findings=ollama_findings(after))
        report["status"], report["stage"] = "passed", "done"
        return 0
    except Exception as exc:  # report the failing stage/check only; private logs stay in --work-dir
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:300]
        return 1
    finally:
        try:
            for path in (root / "runtime/server.json",):
                if path.exists():
                    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                                    str(install / "stop.ps1")], env=env, capture_output=True, timeout=300)
        except Exception:
            pass
        if installed and (install / "unins000.exe").exists():
            subprocess.run([str(install / "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], env=env,
                           capture_output=True, timeout=600)
        clean = sanitize(report, replacements)
        (a.report_dir / "acceptance.json").write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        extra = sorted(p.name for p in a.report_dir.iterdir() if p.name not in UPLOAD_ALLOWLIST)
        for name in extra:   # nothing but the allowlist may sit in the upload folder
            (a.report_dir / name).unlink()
        print(json.dumps({"status": clean["status"], "stage": clean["stage"]}))


if __name__ == "__main__":
    raise SystemExit(main())
