"""Runs INSIDE the installed app's managed Python (venv redirector) on a disposable QA profile.

Order: crt_bootstrap.activate(strict) -> native imports -> real Indra OCR + local llama.cpp inference on two fictional
receipts (one PDF, one JPEG rendered from a fictional PDF so the image-OCR path is exercised) -> reviewed save ->
CSV export through the app's own HTTP endpoints. Writes a PRIVATE JSON (contains paths); the orchestrator sanitizes it.
argv: --app <installed app dir> --fixtures <absolute receipts-100.zip> --fixtures-sha256 <hex> --out <private json>
      --failure <private failure marker> --csv <synthetic csv>
On failure only {"schema": 1, "stage": <enum>, "exception_type": <class name>} is written to --failure (never messages,
tracebacks or paths); the traceback stays on this process's stderr, which the orchestrator keeps private.
"""
import argparse
import hashlib
import io
import json
import secrets
import sys
import time
import zipfile
from pathlib import Path

CASES = ("V001", "V003")  # small fictional receipts from samples/receipts-100-v2
STAGE = ["start"]


def child_modules(pid, crt_bootstrap):
    import ctypes
    from ctypes import wintypes
    k32, psapi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("psapi", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    psapi.EnumProcessModulesEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HMODULE), wintypes.DWORD,
                                           ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
    psapi.EnumProcessModulesEx.restype = wintypes.BOOL
    psapi.GetModuleFileNameExW.argtypes = [wintypes.HANDLE, wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    psapi.GetModuleFileNameExW.restype = wintypes.DWORD
    h = k32.OpenProcess(0x0410, False, pid)   # PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
    if not h:
        raise OSError(ctypes.get_last_error(), "OpenProcess(llama-server)")
    out = []
    try:
        count = 1024
        while True:
            arr, need = (wintypes.HMODULE * count)(), wintypes.DWORD()
            if not psapi.EnumProcessModulesEx(h, arr, ctypes.sizeof(arr), ctypes.byref(need), 0x03):
                raise OSError(ctypes.get_last_error(), "EnumProcessModulesEx(llama-server)")
            if need.value <= ctypes.sizeof(arr):
                break
            count = need.value // ctypes.sizeof(wintypes.HMODULE) + 16
        for i in range(need.value // ctypes.sizeof(wintypes.HMODULE)):
            buf = ctypes.create_unicode_buffer(32768)
            if not psapi.GetModuleFileNameExW(h, arr[i], buf, 32768):
                continue
            out.append({"name": Path(buf.value).name.lower(), "path": buf.value, "class": crt_bootstrap.classify(buf.value),
                        "sha256": crt_bootstrap._sha256(buf.value)})
    finally:
        k32.CloseHandle(h)
    return out


def read_fixtures(path: Path, expected_sha256: str):
    """Read the orchestrator-bound archive once and verify its bytes before use (no search, no cwd assumption)."""
    if not path.is_absolute():
        raise ValueError("fixtures_path_not_absolute")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("fixtures_sha256_mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        manifest = json.loads(z.read("manifest.json"))
        return {c["case_id"]: c for c in manifest["cases"]}, {cid: z.read("individual/" + cid + ".pdf") for cid in CASES}


def run(a):
    sys.path.insert(0, str(a.app))
    from receipt_ledger import config, crt_bootstrap  # stdlib only

    res = {"sys": {"executable": sys.executable, "base_prefix": sys.base_prefix, "prefix": sys.prefix, "version": sys.version}}
    STAGE[0] = "activation"
    res["activation"] = crt_bootstrap.activate(config.RUNTIME_ROOT, config.RUNTIME_PIN, strict=True)

    STAGE[0] = "native_imports"
    import onnxruntime  # noqa: F401  first native import after activation
    import pypdfium2 as pdfium
    from fastapi.testclient import TestClient
    from receipt_ledger import pipeline, runtime
    from receipt_ledger.protection import LedgerKeys
    from receipt_ledger.schemas import Draft
    from receipt_ledger.server import create_app

    STAGE[0] = "fixtures"
    cases, pdfs = read_fixtures(a.fixtures, a.fixtures_sha256)
    with pdfium.PdfDocument(pdfs["V003"]) as doc:   # fictional receipt rendered to an image: no PDF text layer at all
        page = doc[0]
        bitmap = page.render(scale=2)
        jpg = io.BytesIO()
        bitmap.to_pil().convert("RGB").save(jpg, "JPEG", quality=92)
        bitmap.close()
        page.close()
    inputs = [("V001", "V001.pdf", pdfs["V001"], "application/pdf"), ("V003", "V003-rendered.jpg", jpg.getvalue(), "image/jpeg")]

    STAGE[0] = "ledger_setup"
    data = Path(config.DATA_DIR)
    LedgerKeys(data).setup(secrets.token_urlsafe(24))   # throwaway password, never written anywhere
    rt = runtime.LlamaRuntime()
    app = create_app(data, run_workers=False, runtime=rt)
    headers = {"X-Ledger-Request": "1"}
    res["receipts"], server = [], None
    try:
        with TestClient(app) as client:
            boot = app.state.sessions.mint_bootstrap()
            s = client.post("/api/session", headers={**headers, "Authorization": "Bootstrap " + boot})
            s.raise_for_status()
            client.headers["Authorization"] = "Bearer " + s.json()["token"]
            STAGE[0] = "receipts"
            for cid, name, body, mime in inputs:
                up = client.post("/api/receipts", files={"files": (name, body, mime)}, headers=headers)
                up.raise_for_status()
                rid = up.json()["imported"][0]["id"]
                t = time.perf_counter()
                pipeline.process(app.state.store, rid, runtime=rt)
                client.get("/api/receipts/" + rid).raise_for_status()
                row = app.state.store.get(rid)   # full stored OCR/suggestion evidence (the HTTP view may summarize)
                if server is None and rt._proc is not None and rt._proc.poll() is None:
                    server = {"modules": child_modules(rt._proc.pid, crt_bootstrap)}
                ocr, sug = row.get("ocr") or {}, row.get("suggestion") or {}
                rec = {"case_id": cid, "input": name, "seconds": round(time.perf_counter() - t, 2), "status": row.get("status"),
                       "error_present": bool(row.get("error")), "ocr_engine": ocr.get("engine"),
                       "ocr_page_sources": [p.get("source") for p in ocr.get("pages", [])],
                       "ocr_lines": sum(len(p.get("lines", [])) for p in ocr.get("pages", [])),
                       "ocr_text_chars": len(ocr.get("text", "")), "native_pdf_text_chars": len(ocr.get("native_pdf_text", "")),
                       "origin": sug.get("origin"), "account": sug.get("account"), "model": sug.get("model"),
                       "model_digest": sug.get("model_digest"), "runtime": sug.get("runtime"),
                       "runtime_pin_sha256": sug.get("runtime_pin_sha256"),
                       "raw_model_candidate_is_object": isinstance(sug.get("raw_model_candidate"), dict),
                       "raw_model_candidate_keys": sorted(sug["raw_model_candidate"]) if isinstance(sug.get("raw_model_candidate"), dict) else None}
                res["receipts"].append(rec)
                c = cases[cid]   # reviewed save: fictional oracle values stand in for the human review step
                d = Draft(date=c["date"], merchant=c["merchant"], total=c["total"], account=sug.get("account") or "消耗品費",
                          payment="cash", business_percent=100, description=c["title"], purpose="CI acceptance fictional record",
                          export_tax="outside")
                saved = client.post("/api/receipts/" + rid + "/save", json={"version": row["version"], "draft": d.model_dump(), "confirm": True},
                                    headers=headers)
                saved.raise_for_status()
            STAGE[0] = "csv_export"
            csv_response = client.get("/api/export/expenses")
            csv_response.raise_for_status()
            a.csv.write_bytes(csv_response.content)
            res["csv"] = {"sha256": hashlib.sha256(csv_response.content).hexdigest(), "bytes": len(csv_response.content),
                          "data_rows": max(0, len(csv_response.content.decode("utf-8-sig").strip().splitlines()) - 1)}
        res["runtime_status"] = {k: v for k, v in rt.status().items() if isinstance(v, (str, int, float, bool)) or v is None}
        res["cpu_args"] = runtime.launch_arguments("llama-server.exe", "<model>.gguf", 0, config.RUNTIME_THREADS, config.RUNTIME_CTX)[1:]
        STAGE[0] = "runtime_close"   # every step above succeeded; only the shutdown remains
    finally:
        rt.close()   # on an earlier failure the stage that failed is kept
    STAGE[0] = "write_result"
    res["llama_server"] = server
    res["python_modules"] = crt_bootstrap.loaded_modules()
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("child-ok", len(res["receipts"]))


def write_failure_marker(path: Path, stage: str, exc: BaseException) -> None:
    """Allowlisted fields only; the orchestrator re-validates them before anything leaves the private work dir."""
    path.write_text(json.dumps({"schema": 1, "stage": stage, "exception_type": type(exc).__name__}), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", type=Path, required=True)
    ap.add_argument("--fixtures", type=Path, required=True)
    ap.add_argument("--fixtures-sha256", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--failure", type=Path, required=True)
    ap.add_argument("--csv", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        run(a)
    except BaseException as exc:
        try:
            write_failure_marker(a.failure, STAGE[0], exc)
        except Exception:
            pass
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
