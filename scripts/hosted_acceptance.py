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
from verify_windows_package import (SHORTCUT_NAMES, icon_entries_from_log, known_programs_dir,  # noqa: E402,F401
                                    resolve_shortcut_group, group_candidates)   # one shared Inno-log shortcut binding

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


GROUP_NAME = "Ryoshu"   # packaging/windows/ryoshu.iss DefaultGroupName (no /GROUP is passed: the product default is under test)


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


# ----------------------------------------------------------------------------------------------- receipt child contract
# The child writes a PRIVATE marker {"schema": 1, "stage": <enum>, "exception_type": <identifier>} when it fails. Only these
# allowlisted values (never exception text, tracebacks or paths) may reach the public acceptance.json.
CHILD_STAGES = ("start", "activation", "native_imports", "fixtures", "ledger_setup", "receipts", "csv_export",
                "runtime_close", "write_result")
CHILD_REASONS = {
    "start": "child failed before CRT activation",
    "activation": "strict CRT activation failed",
    "native_imports": "native library import failed after activation",
    "fixtures": "fixture archive could not be verified or read",
    "ledger_setup": "QA ledger or runtime setup failed",
    "receipts": "receipt upload, OCR, inference or reviewed save failed",
    "csv_export": "CSV export failed",
    "runtime_close": "local runtime shutdown failed",
    "write_result": "private result could not be written",
}
# Explicit vocabulary of exception class names this child can realistically raise (builtins + its own dependencies).
# Only these are published verbatim; any other value (however identifier-shaped) becomes the fixed category below,
# so nothing from the private marker is ever echoed.
APPROVED_EXCEPTION_TYPES = frozenset({
    "AssertionError", "AttributeError", "BadZipFile", "ConnectionError", "CrtActivationError", "FileNotFoundError",
    "HTTPStatusError", "ImportError", "IsADirectoryError", "JSONDecodeError", "KeyError", "KeyboardInterrupt",
    "MemoryError", "ModuleNotFoundError", "NotADirectoryError", "OSError", "PdfiumError", "PermissionError",
    "ProtectionError", "RuntimeError", "RuntimeFailure", "SystemExit", "TimeoutError", "TypeError",
    "UnicodeDecodeError", "ValidationError", "ValueError",
})
UNAPPROVED_EXCEPTION_TYPE = "unapproved_exception_type"


def public_exception_type(value) -> str | None:
    """Approved class name, the fixed non-echoing category for anything else present, or None when absent."""
    if value is None:
        return None
    return value if isinstance(value, str) and value in APPROVED_EXCEPTION_TYPES else UNAPPROVED_EXCEPTION_TYPE


# ----------------------------------------------------------------------------------------------- receipt format gate
# The child processes exactly these fictional inputs (hosted_acceptance_child.CASES / inputs).
CASE_INPUTS = {"V001": ("V001.pdf", "pdf"), "V003": ("V003-rendered.jpg", "jpeg")}
OCR_ENGINE = "Indra OCR"
# indra_ocr.pipeline.Page.source contract (1.1.2): "ocr" | "text" (PDF text layer) | "hybrid" (text layer + OCR) | "error".
PAGE_SOURCES = ("text", "ocr", "hybrid", "error")
RECEIPT_STATUSES = ("review", "error", "queued", "processing", "confirmed", "excluded")


def _count(value):
    return value if type(value) is int and value >= 0 else None


def project_receipt(row, model_sha256: str, pin_sha256: str) -> dict:
    """Fixed-allowlist public metadata for one child receipt row. Unknown values become static categories/null;
    no OCR text, account names, paths or other free strings are copied."""
    row = row if isinstance(row, dict) else {}
    case_id = row.get("case_id") if row.get("case_id") in CASE_INPUTS else "unknown_case"
    expected = CASE_INPUTS.get(case_id)
    sources = row.get("ocr_page_sources")
    return {
        "case_id": case_id,
        "input_kind": expected[1] if expected and row.get("input") == expected[0] else "unexpected_input",
        "ocr_engine_is_indra": row.get("ocr_engine") == OCR_ENGINE,
        "ocr_page_sources": [s if s in PAGE_SOURCES else "unknown_source" for s in sources] if isinstance(sources, list) else None,
        "ocr_lines": _count(row.get("ocr_lines")), "ocr_text_chars": _count(row.get("ocr_text_chars")),
        "native_pdf_text_chars": _count(row.get("native_pdf_text_chars")),
        "status": row.get("status") if row.get("status") in RECEIPT_STATUSES else "unknown_status",
        "error_present": row.get("error_present") if type(row.get("error_present")) is bool else None,
        "origin_is_model": row.get("origin") == "model",
        "raw_model_candidate_is_object": row.get("raw_model_candidate_is_object") is True,
        "model_digest_matches_pin": row.get("model_digest") == model_sha256,
        "runtime_pin_matches": row.get("runtime_pin_sha256") == pin_sha256,
    }


def ocr_route(p: dict) -> tuple[bool, str, str]:
    """(ok, route, reason) for a projected receipt. PDFs may use Indra's text-layer, OCR or hybrid route; a JPEG must be
    real image OCR (only 'ocr' pages, no PDF text layer)."""
    if p["case_id"] == "unknown_case" or p["input_kind"] == "unexpected_input":
        return False, "none", "unexpected_case_or_input"
    if not p["ocr_engine_is_indra"]:
        return False, "none", "wrong_ocr_engine"
    if not p["ocr_lines"] or not p["ocr_text_chars"] or p["native_pdf_text_chars"] is None:
        return False, "none", "empty_or_malformed_counts"
    sources = p["ocr_page_sources"]
    if not sources:
        return False, "none", "missing_page_sources"
    if any(s not in ("text", "ocr", "hybrid") for s in sources):
        return False, "none", "unknown_or_error_page_source"
    if p["input_kind"] == "jpeg":
        if any(s != "ocr" for s in sources):
            return False, "image_not_ocr", "image_input_without_image_ocr"
        if p["native_pdf_text_chars"] != 0:
            return False, "image_ocr", "image_input_has_native_pdf_text"
        return True, "image_ocr", "ok"
    route = "pdf_text" if set(sources) == {"text"} else "pdf_ocr" if set(sources) == {"ocr"} else "pdf_hybrid"
    if route != "pdf_ocr" and p["native_pdf_text_chars"] == 0:
        return False, route, "text_route_without_native_pdf_text"
    return True, route, "ok"


def check_record(name, ok, /, **detail) -> dict:
    """One acceptance check. Identity and outcome come ONLY from the positional arguments; every keyword is diagnostic
    metadata kept under 'detail', so an unpacked dict carrying 'ok'/'check'/'name' can neither raise nor override them."""
    if not isinstance(name, str) or not name:
        raise ValueError("check_name_required")
    return {"check": name, "ok": bool(ok), "detail": dict(detail)}


def bind_fixture(path: Path) -> dict:
    """Resolve the caller's fixture path NOW (relative to the caller's cwd), require a regular file, bind its bytes."""
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file():
        raise FileNotFoundError("fixtures_not_a_regular_file")
    return {"path": resolved, "sha256": sha(resolved), "bytes": resolved.stat().st_size}


def child_failure_summary(marker: Path, returncode: int, result_exists: bool) -> dict | None:
    """Allowlisted, static description of a failed receipt child; None when it succeeded with a result."""
    if returncode == 0 and result_exists:
        return None
    data = None
    try:
        if marker.is_file() and marker.stat().st_size <= 4096:
            data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    stage = data.get("stage") if isinstance(data, dict) and data.get("schema") == 1 and data.get("stage") in CHILD_STAGES else None
    etype = public_exception_type(data.get("exception_type")) if stage else None
    if stage is None:
        reason = "child exited without a result" if returncode == 0 else "child exited before writing an allowlisted diagnostic marker"
        return {"stage": "unknown_before_marker", "exception_type": None, "reason": reason, "exit_code": returncode}
    return {"stage": stage, "exception_type": etype, "reason": CHILD_REASONS[stage], "exit_code": returncode}


def child_command(python: Path, child_script: Path, install: Path, fixture: dict, private: Path, failure: Path, csv_out: Path) -> list[str]:
    """Every path handed to the child is absolute: the child runs with cwd=<work>, never the caller's directory."""
    for p in (python, child_script, install, fixture["path"], private, failure, csv_out):
        if not Path(p).is_absolute():
            raise ValueError("child_path_not_absolute")
    return [str(python), "-I", "-B", str(child_script), "--app", str(install), "--fixtures", str(fixture["path"]),
            "--fixtures-sha256", fixture["sha256"], "--out", str(private), "--failure", str(failure), "--csv", str(csv_out)]


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
    report_dir = a.report_dir.resolve()   # absolute before any child runs with a different cwd
    profile = work / "QA 領収書 profile"
    install = profile / "AppData/Local/Programs/StudioYebisu/Ryoshu"
    root = profile / "StudioYebisu/ReceiptLedger"
    # Where Inno's {group} lands for this per-user install is taken from the installer's OWN log (Dest filename of each
    # icon entry) and must be one of the two per-user candidates; the all-users folder is reported but never accepted.
    # No Known Folder / APPDATA / profile setting is changed. Neither per-user candidate may pre-exist (no takeover).
    try:
        common = known_programs_dir("{0139D44E-6AFE-49F2-8690-3DAFCAE6FFB8}")
    except OSError:
        common = None
    candidates = group_candidates(profile, known_programs_dir(), common)
    for alias_name in ("known_user_programs", "isolated_profile_programs"):
        if candidates[alias_name].exists():
            ap.error(f"A Start Menu 'Ryoshu' group already exists ({alias_name}); refusing to overwrite it")
    group_dir = None
    env = {**os.environ, "USERPROFILE": str(profile), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    for k in [k for k in env if k.upper() in ("RECEIPT_LEDGER_RUNTIME_ROOT", "RECEIPT_LEDGER_DATA", "HF_HOME")]:
        env.pop(k)
    report = {"schema": "ryoshu.hosted-windows-acceptance/1", "status": "failed", "stage": "start",
              "not_benchmark": "CI acceptance only; never merged into the 100-receipt benchmark statistics",
              "runner": runner_facts(), "checks": []}
    replacements = {str(install): "<install>", str(root): "<root>", str(profile): "<profile>", str(work): "<work>",
                    os.environ.get("SystemRoot", r"C:\Windows"): "<SystemRoot>", str(Path(__file__).resolve().parents[1]): "<checkout>",
                    **{str(p.parent): f"<{a}>" for a, p in candidates.items()}, str(Path.home()): "<home>"}

    def check(name, ok, /, **detail):
        record = check_record(name, ok, **detail)
        report["checks"].append(record)
        ok = record["ok"]
        if not ok:
            raise AssertionError(name)

    installed = False
    try:
        report["stage"] = "preflight"
        before = probe_ollama()
        check("ollama_absent_before", not ollama_findings(before), findings=ollama_findings(before))
        # The workflow passes a repository-relative fixture path; bind it here (caller's cwd) before any cwd change.
        fixture = bind_fixture(a.fixtures)
        report["fixtures"] = {"sha256": fixture["sha256"], "bytes": fixture["bytes"]}
        check("fixtures_bound_before_child", fixture["path"].is_absolute() and fixture["bytes"] > 0)
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
        log_text = (work / "install.log").read_text(encoding="utf-8-sig", errors="replace")   # private; only aliases leave
        shortcut = resolve_shortcut_group(icon_entries_from_log(log_text), candidates)
        report["shortcut_diagnostics"] = shortcut
        if shortcut["selected_group_alias"] in candidates:
            group_dir = candidates[shortcut["selected_group_alias"]]
        check("start_menu_shortcuts", shortcut["ok"], selected_group_alias=shortcut["selected_group_alias"])
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
        failure = work / "child-failure.json"
        csv_out = report_dir / "synthetic-expenses.csv"
        cenv = {k: v for k, v in env.items() if k.upper() in ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS",
                                                             "PROCESSOR_ARCHITECTURE", "COMPUTERNAME", "USERPROFILE",
                                                             "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "PYTHONUTF8", "PYTHONIOENCODING")}
        cenv.update(PATH=os.path.join(os.environ["SystemRoot"], "System32") + ";" + os.environ["SystemRoot"],
                    RECEIPT_LEDGER_DATA=str(root / "qa-ledger/data"), HF_HUB_OFFLINE="1")
        command = child_command(root / "venv/Scripts/python.exe", Path(__file__).resolve().with_name("hosted_acceptance_child.py"),
                                install, fixture, private, failure, csv_out)
        child = subprocess.run(command, env=cenv, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600)
        (work / "child.log").write_text(child.stdout + "\n" + child.stderr, encoding="utf-8")   # private, never uploaded
        summary = child_failure_summary(failure, child.returncode, private.is_file())
        if summary is not None:
            report["child_failure"] = summary   # allowlisted stage/type/static reason only
        check("receipt_child_succeeded", child.returncode == 0 and summary is None, exit_code=child.returncode)
        res = json.loads(private.read_text(encoding="utf-8"))
        # Persist the fixed-allowlist projection BEFORE any receipt assertion so a failing gate still reports why.
        rows = res.get("receipts") if isinstance(res.get("receipts"), list) else []
        report["receipts"] = [project_receipt(r, pin["model"]["sha256"], pin_sha) for r in rows]
        for p in report["receipts"]:
            p["ocr_route"], p["ocr_route_reason"] = ocr_route(p)[1:]
        check("receipts_present_for_every_case", sorted(p["case_id"] for p in report["receipts"]) == sorted(CASE_INPUTS),
              receipts=len(report["receipts"]))
        check("strict_crt_activation_active", res["activation"]["state"] == "active")
        for r, p in zip(res["receipts"], report["receipts"]):
            ok, route, reason = ocr_route(p)
            check(f"indra_ocr_route_{p['case_id']}_{p['input_kind']}", ok, route=route, reason=reason)
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
              and not any(candidates[n].exists() for n in ("known_user_programs", "isolated_profile_programs"))
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
        (report_dir / "acceptance.json").write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        extra = sorted(p.name for p in report_dir.iterdir() if p.name not in UPLOAD_ALLOWLIST)
        for name in extra:   # nothing but the allowlist may sit in the upload folder
            (report_dir / name).unlink()
        print(json.dumps({"status": clean["status"], "stage": clean["stage"]}))


if __name__ == "__main__":
    raise SystemExit(main())
