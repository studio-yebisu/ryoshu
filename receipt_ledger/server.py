import asyncio
import importlib.metadata
import json
import os
import sqlite3
import tempfile
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, config
from .accounting import amounts, csv_export, journal, summary
from .exports import PROFILES, TAX_CHOICES, build_export
from .pipeline import local_models, process, validate_file
from .schemas import SaveRequest, VersionRequest, confirmation_errors
from .store import Conflict, Missing, Store, now


def create_app(data_dir=None, run_workers=True):
    store = Store(Path(data_dir or config.DATA_DIR))
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="receipt")
    import_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        yield
        executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="Ryoshu", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in ["GET", "HEAD"]:
            origin = request.headers.get("origin")
            allowed = {f"http://127.0.0.1:{config.PORT}", f"http://localhost:{config.PORT}"}
            if request.headers.get("x-ledger-request") != "1" or (origin and origin not in allowed) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "このアプリの画面から操作してください"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        return response

    @app.exception_handler(Missing)
    async def missing(request, exc):
        return JSONResponse({"detail": "領収書が見つかりません"}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ValueError)
    async def bad_value(request, exc):
        return JSONResponse({"detail":str(exc)},status_code=422)

    def enqueue(receipt_id):
        if run_workers:
            executor.submit(process, store, receipt_id)

    def decorate(row, detail=True):
        row["business_amount"], row["private_amount"] = amounts(row["draft"])
        row["journal"] = journal(row["draft"])
        row["preview_count"] = len(list(store.folder(row["id"]).glob("preview-*.jpg")))
        if not detail and row["ocr"]:
            row["ocr"] = {k: v for k, v in row["ocr"].items() if k not in ["text", "pages"]}
        return row

    def sample_set():
        return config.SAMPLE_DIR, "receipts-100", {"count": 100, "version": "receipts-100-v2", "description": "6モデルの独立した帳票設計。電子PDF、スキャン、薄い印字、FAX、斜め撮影、回転を含みます。"}

    @app.get("/api/health")
    def health():
        try:
            models = local_models()
            installed = any(m.get("name") == config.MODEL for m in models)
            ollama = True
        except Exception:
            installed = ollama = False
        return {"app": "receipt-ledger", "app_version": __version__, "ocr_version": importlib.metadata.version("indra-ocr"), "model": config.MODEL,
                "environment_label":os.environ.get('RECEIPT_LEDGER_ENV_LABEL',''),
                "model_ready": installed, "ollama_ready": ollama, "compute": "CPU", "data_dir": str(store.root),
                "schema_version":2,"account_count":len(store.accounts(False)),"policy_count":len(store.policies()),
                "privacy": "loopback-only; local OCR and local model; offline OCR cache"}

    @app.get("/api/config")
    def app_config():
        return {"accounts": store.accounts(), "payments": config.PAYMENTS, "tax_rates": config.TAX_LABELS,
                "business_context": store.business_context(), "rules": store.rules(),
                "samples": sample_set()[2],
                "export_profiles":PROFILES,"export_tax_choices":TAX_CHOICES,
                "export_account_maps":{p:store.export_map(p) for p in ['freee','mf','yayoi']},
                "limits":{"max_files":config.MAX_FILES,"max_bytes":config.MAX_BYTES,"max_pages":config.MAX_PAGES}}

    @app.get("/api/catalog")
    def catalog():
        return store.catalog()

    class AccountRequest(BaseModel):
        model_config=ConfigDict(extra="forbid")
        name:str=Field(min_length=1,max_length=100)
        kind:Literal["expense","asset"]="expense"
        description:str=Field(default="",max_length=1000)
        aliases:list[str]=Field(default_factory=list,max_length=10)
        review_questions:list[str]=Field(default_factory=list,max_length=10)
        keywords:list[str]=Field(default_factory=list,max_length=20)

    @app.post("/api/accounts")
    def add_account(body:AccountRequest):
        if any(len(s)>200 for s in body.aliases+body.review_questions+body.keywords):
            raise ValueError("別名・確認事項は各200文字以内で入力してください")
        return store.add_account(body.name,body.kind,body.description,body.aliases,body.review_questions,body.keywords)

    @app.get("/api/samples/{kind}")
    def sample_download(kind:str):
        root, prefix, _ = sample_set()
        names={"zip":prefix+".zip","review":prefix+"-review.pdf","manifest":"manifest.json"}
        if kind not in names:
            raise HTTPException(404)
        path=root/names[kind]
        if not path.is_file():
            raise HTTPException(404,"サンプルが見つかりません")
        return FileResponse(path,filename=names[kind],content_disposition_type="inline" if kind=="review" else "attachment")

    class SettingsRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        business_context: str = Field(max_length=1500)

    @app.post("/api/settings")
    def settings(body: SettingsRequest):
        return {"business_context": store.business_context(body.business_context)}

    class RuleRequest(BaseModel):
        merchant: str = Field(max_length=200)

    @app.post("/api/rules/remove")
    def remove_rule(body: RuleRequest):
        store.remove_rule(body.merchant)
        return {"rules": store.rules()}

    @app.get("/api/receipts")
    def receipts():
        return {"receipts": [decorate(r, detail=False) for r in store.list()]}

    @app.post("/api/receipts")
    async def upload(request: Request):
        # Starlette spools large files to the OS temp directory; bounded reads prevent unbounded RAM use.
        try:
            form = await request.form(max_files=config.MAX_FILES, max_fields=5, max_part_size=config.MAX_BYTES)
        except Exception as exc:
            raise HTTPException(400, f"一度に{config.MAX_FILES}ファイルまで選んでください") from exc
        results, errors = [], []
        files = form.getlist("files")
        if not files:
            raise HTTPException(400, "領収書の画像・PDFを選んでください")
        for item in files:
            if not hasattr(item, "read"):
                continue
            name = (item.filename or "receipt").replace("\\", "/").split("/")[-1][:200]
            try:
                raw = await item.read(config.MAX_BYTES + 1)
                extension = await asyncio.to_thread(validate_file, raw)
                with import_lock:
                    row, duplicate = store.add(name, extension, raw)
                if not duplicate:
                    enqueue(row["id"])
                results.append({"id": row["id"], "duplicate": duplicate, "name": name})
            except ValueError as exc:
                errors.append({"name": name, "message": str(exc)})
            finally:
                await item.close()
        return {"imported": results, "errors": errors}

    @app.get("/api/receipts/{receipt_id}")
    def receipt(receipt_id: str):
        return decorate(store.get(receipt_id))

    @app.post("/api/receipts/{receipt_id}/save")
    def save(receipt_id: str, body: SaveRequest):
        if body.confirm:
            errors = confirmation_errors(body.draft)
            if errors:
                raise HTTPException(422, errors)
        return decorate(store.save(receipt_id, body.version, body.draft, body.confirm, body.remember_account))

    @app.post("/api/receipts/{receipt_id}/{action}")
    def action(receipt_id: str, action: str, body: VersionRequest):
        if action not in ["retry", "exclude", "restore"]:
            raise HTTPException(404)
        row = store.set_status(receipt_id, body.version, action)
        if action == "retry":
            enqueue(receipt_id)
        return decorate(row)

    @app.get("/api/receipts/{receipt_id}/original")
    def original(receipt_id: str):
        row = store.get(receipt_id)
        return FileResponse(store.folder(receipt_id) / ("original" + row["extension"]), filename=row["original_name"])

    @app.get("/api/receipts/{receipt_id}/preview/{page}")
    def preview(receipt_id: str, page: int):
        if not 0 <= page < config.MAX_PAGES:
            raise HTTPException(404)
        path = store.folder(receipt_id) / f"preview-{page}.jpg"
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/receipts/{receipt_id}/history")
    def history(receipt_id: str):
        return {"history": store.history(receipt_id)}

    def check_month(month):
        import re
        if month and not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month):
            raise HTTPException(422, "年月の指定が正しくありません")

    @app.get("/api/summary")
    def totals(month: str = ""):
        check_month(month)
        return summary(store.list(), month)

    @app.get("/api/export-preview")
    def export_preview(profile:str='generic',kind:str='journal',month:str='',tax_mode:str='reviewed'):
        check_month(month)
        return build_export(store.list(),profile,month,kind,tax_mode,store.export_map(profile)).preview()

    class ExportMapRequest(BaseModel):
        model_config=ConfigDict(extra='forbid')
        profile:Literal['freee','mf','yayoi']
        names:dict[str,str]=Field(max_length=100)

    @app.post('/api/export-map')
    def export_map(body:ExportMapRequest):
        return {'names':store.export_map(body.profile,body.names)}

    @app.get("/api/export/{kind}")
    def export(kind: str, month: str = "", profile:str='generic',tax_mode:str='reviewed'):
        check_month(month)
        if kind not in ["expenses", "journal", "review"]:
            raise HTTPException(404)
        result=build_export(store.list(),profile,month,kind,tax_mode,store.export_map(profile))
        content=result.content()
        encoding='shift_jis' if result.encoding=='cp932' else 'utf-8'
        return Response(content, media_type=f"text/csv; charset={encoding}", headers={"Content-Disposition": f'attachment; filename="{profile}-{kind}-{month or "all"}.csv"'})

    @app.get("/api/backup")
    def backup():
        # Back up a consistent SQLite snapshot and immutable original files.
        with tempfile.TemporaryDirectory(prefix="receipt-ledger-backup-") as temp:
            snapshot = Path(temp) / "ledger.sqlite3"
            with store.connect() as source:
                target = sqlite3.connect(snapshot)
                try:
                    source.backup(target)
                finally:
                    target.close()
            with closing(sqlite3.connect(snapshot)) as db:
                originals = db.execute("SELECT id,extension,sha256 FROM receipts").fetchall()
            archive = Path(temp) / "backup.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(snapshot, "data/ledger.sqlite3")
                for receipt_id, extension, digest in originals:
                    original_path = store.root / "receipts" / receipt_id / ("original" + extension)
                    import hashlib
                    if hashlib.sha256(original_path.read_bytes()).hexdigest() != digest:
                        raise HTTPException(409, "原本のハッシュが変わっています。保存先を確認してください")
                    bundle.write(original_path, f"data/receipts/{receipt_id}/original{extension}")
                bundle.writestr("manifest.json", json.dumps({"app": "Ryoshu", "version": __version__, "created_at": now(), "receipts": len(originals), "contains": "SQLite snapshot (OCR, suggestions, edits, rules, audit) and original receipts. Previews regenerate on retry."}, ensure_ascii=False, indent=2))
            return Response(archive.read_bytes(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="receipt-ledger-backup.zip"'})

    app.mount("/evidence", StaticFiles(directory=config.APP_DIR / "docs/testing", html=True, check_dir=False), name="evidence")
    app.mount("/samples", StaticFiles(directory=config.APP_DIR / "samples", check_dir=False), name="samples")
    app.mount("/", StaticFiles(directory=config.APP_DIR / "web", html=True), name="web")
    return app
