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
from .schemas import Draft, SaveRequest, VersionRequest, confirmation_errors
from .store import Conflict, Missing, Store, now
from .workflow import ledger_records, review_before_ledger, recover_queue, export_tax_mode
from .importing import Importer
from .upload_limit import UploadSizeLimit, UploadTooLarge
from .listing import list_summaries, possible_duplicates, duplicate_warnings
from .backups import create_backup
from .export_history import ExportHistory
from starlette.background import BackgroundTask
from .previews import ensure_preview
from .openai_oauth import OpenAIConnection, ChatConnectionError
from .chat import ChatEdits, ChatRequest, ApplyRequest
from filelock import Timeout as FileLockTimeout


def create_app(data_dir=None, run_workers=True, auth_dir=None):
    store = Store(Path(data_dir or config.DATA_DIR))
    importer = Importer(store)
    export_history = ExportHistory(store)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="receipt")
    import_lock = threading.Lock()
    connection = OpenAIConnection(auth_dir or config.DEFAULT_ROOT / "auth")
    chat = ChatEdits(store, connection)

    @asynccontextmanager
    async def lifespan(app):
        if run_workers:
            for receipt_id in recover_queue(store):
                enqueue(receipt_id)
        else:
            store.recover()
        yield
        connection.close()
        executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="Ryoshu", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.openai = connection
    app.state.chat = chat
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(UploadSizeLimit, max_bytes=config.MAX_REQUEST_BYTES)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "このアプリの画面から操作してください"}, status_code=403)
        if request.url.path.startswith("/api/") and request.method not in ["GET", "HEAD"]:
            origin = request.headers.get("origin")
            allowed = {f"http://127.0.0.1:{config.PORT}", f"http://localhost:{config.PORT}"}
            if request.headers.get("x-ledger-request") != "1" or (origin and origin not in allowed) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "このアプリの画面から操作してください"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
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

    @app.exception_handler(ChatConnectionError)
    async def chat_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    @app.exception_handler(FileLockTimeout)
    async def auth_busy(request, exc):
        return JSONResponse({"detail": "別のRyoshuが認証情報を更新中です。少し待ってからもう一度操作してください。"}, status_code=409)

    class ConnectRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        profile_id: str | None = Field(default=None, max_length=100)
        reconsent: bool = False

    class AccountRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        profile_id: str = Field(min_length=1, max_length=100)

    @app.get("/api/chat/connection")
    def chat_connection():
        return connection.status()

    @app.post("/api/chat/connect")
    def chat_connect(body: ConnectRequest):
        return connection.start(body.profile_id, body.reconsent)

    @app.post("/api/chat/cancel-login")
    def chat_cancel_login():
        connection.close(message="接続待ちを終了しました。必要なときは接続ボタンからやり直せます。")
        return {"cancelled": True}

    @app.post("/api/chat/reset-registration")
    def chat_reset_registration():
        connection.reset_registration()
        return connection.status()

    @app.post("/api/chat/account")
    def chat_account(body: AccountRequest):
        connection.select(body.profile_id)
        return connection.status()

    @app.post("/api/chat/disconnect")
    def chat_disconnect(body: AccountRequest):
        return connection.disconnect(body.profile_id)

    @app.post("/api/chat/models")
    def chat_models():
        return {"models": connection.models()}

    @app.post("/api/chat/propose")
    def chat_propose(body: ChatRequest):
        return chat.propose(body)

    @app.post("/api/chat/apply")
    def chat_apply(body: ApplyRequest):
        return chat.apply(body.proposal_id)

    def enqueue(receipt_id):
        if run_workers:
            executor.submit(process, store, receipt_id)

    def decorate(row, detail=True, duplicate_map=None):
        row["business_amount"], row["private_amount"] = amounts(row["draft"])
        if row["draft"]["total"] is None or row["draft"]["business_percent"] is None:
            row["business_amount"] = row["private_amount"] = None
        row["journal"] = journal(row["draft"])
        if "preview_count" not in row:
            row["preview_count"] = (row.get("ocr") or {}).get("page_count") or 1
        row["preview_count"] = max(1, min(config.MAX_PAGES, row["preview_count"]))
        if "source_document" not in row:
            row["source_document"] = importer.source(row["id"])
        if detail:
            row["source_documents"] = importer.sources(row["id"])
        row["possible_duplicate_ids"] = (duplicate_map if duplicate_map is not None else duplicate_warnings(store)).get(row["id"], [])
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
                "environment_label":os.environ.get('RECEIPT_LEDGER_ENV_LABEL',''), "source_dir": str(Path(__file__).resolve().parents[1]),
                "model_ready": installed, "ollama_ready": ollama, "compute": "CPU", "data_dir": str(store.root),
                "schema_version":2,"account_count":len(store.accounts(False)),"policy_count":len(store.policies()),
                "privacy": "loopback-only; local OCR and local model; offline OCR cache"}

    @app.get("/api/config")
    def app_config():
        return {"accounts": store.accounts(), "payments": config.PAYMENTS, "tax_rates": config.TAX_LABELS,
                "business_context": store.business_context(), "rules": store.rules(),
                "review_before_ledger": review_before_ledger(store),
                "export_tax_mode": export_tax_mode(store),
                "chat": {"supported": True, "provider": "openai-oauth", "max_rows": 50},
                "samples": sample_set()[2],
                "export_profiles":PROFILES,"export_tax_choices":TAX_CHOICES,
                "export_account_maps":{p:store.export_map(p) for p in ['freee','mf','yayoi']},
                "limits":{"max_files":config.MAX_FILES,"max_bytes":config.MAX_BYTES,"max_pages":config.MAX_PAGES,
                          "max_split_pages":config.MAX_SPLIT_PAGES,"max_request_bytes":config.MAX_REQUEST_BYTES}}

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
        business_context: str | None = Field(default=None, max_length=1500)
        review_before_ledger: bool | None = None
        export_tax_mode: Literal["reviewed", "exempt"] | None = None

    @app.post("/api/settings")
    def settings(body: SettingsRequest):
        return {"business_context": store.business_context(body.business_context),
                "review_before_ledger": review_before_ledger(store, body.review_before_ledger),
                "export_tax_mode": export_tax_mode(store, body.export_tax_mode)}

    class RuleRequest(BaseModel):
        merchant: str = Field(max_length=200)

    @app.post("/api/rules/remove")
    def remove_rule(body: RuleRequest):
        store.remove_rule(body.merchant)
        return {"rules": store.rules()}

    @app.get("/api/receipts")
    def receipts():
        rows = list_summaries(store)
        duplicates = possible_duplicates(rows)
        return {"receipts": [decorate(r, detail=False, duplicate_map=duplicates) for r in rows]}

    @app.get("/api/ledger")
    def ledger(month: str = ""):
        before = review_before_ledger(store)
        rows = ledger_records(list_summaries(store), month, before)
        duplicates = possible_duplicates(rows)
        return {"rows": [decorate(r, detail=False, duplicate_map=duplicates) for r in rows],
                "unreviewed_count": sum(r["status"] != "confirmed" for r in rows),
                "review_before_ledger": before}

    class ReviewRow(VersionRequest):
        model_config = ConfigDict(extra="forbid")
        id: str = Field(min_length=1, max_length=40)

    class LedgerReviewRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        rows: list[ReviewRow] = Field(min_length=1, max_length=500)
        purpose: str = Field(default="", max_length=1000)
        payment: Literal["", "owner", "cash", "bank", "card"] = ""
        business_percent: int | None = Field(default=None, ge=1, le=100, strict=True)
        export_tax: Literal["", "outside", "non_taxable", "purchase10", "purchase8", "transition10", "transition8"] = ""

    @app.post("/api/ledger/review")
    def review_ledger(body: LedgerReviewRequest):
        if len({r.id for r in body.rows}) != len(body.rows):
            raise ValueError("同じ明細が重複しています")
        reviewed, issues = [], []
        for selected in body.rows:
            try:
                row = store.get(selected.id)
                draft = Draft(**row["draft"])
                if row["status"] == "confirmed":
                    raise Conflict("既に確認済みです。画面を更新してください")
                if row["status"] == "excluded":
                    raise Conflict("対象外の明細です。先に対象へ戻してください")
                if body.export_tax and not draft.export_tax and draft.tax_rate == "mixed":
                    raise ValueError("複数税率の明細は税区分をまとめて入力できません。原本と会計ソフトでの分割方法を個別に確認してください")
                updates = {key: getattr(body, key) for key in ("purpose", "payment", "business_percent", "export_tax")
                           if getattr(draft, key) in (None, "") and getattr(body, key) not in (None, "")}
                saved = store.save(selected.id, selected.version, draft.model_copy(update=updates), confirm=True)
                reviewed.append(saved["id"])
            except (Conflict, Missing, ValueError) as exc:
                issues.append({"id": selected.id, "message": str(exc) if not isinstance(exc, Missing) else "明細が見つかりません"})
        return {"reviewed": reviewed, "issues": issues}

    @app.post("/api/receipts")
    async def upload(request: Request):
        # Starlette spools large files to the OS temp directory; bounded reads prevent unbounded RAM use.
        try:
            form = await request.form(max_files=config.MAX_FILES, max_fields=5, max_part_size=config.MAX_BYTES)
        except UploadTooLarge:
            raise
        except Exception as exc:
            if request.scope.get("ryoshu_upload_limit"):
                raise HTTPException(413, "送信量が大きすぎます。ファイルを分けて取り込んでください。保存済みの原本は保持しています。") from exc
            raise HTTPException(400, f"ファイルを受け取れませんでした。一度に{config.MAX_FILES}ファイルまでに分けて、選び直してください") from exc
        results, errors = [], []
        try:
            files = form.getlist("files")
            mode = form.get("pdf_mode", "document")
            if mode not in ("document", "pages"):
                raise HTTPException(400, "PDFの取り込み方を選び直してください")
            if not files or any(not hasattr(item, "read") for item in files):
                raise HTTPException(400, "領収書の画像・PDFを選んでください")
            for item in files:
                name = (item.filename or "receipt").replace("\\", "/").split("/")[-1][:200]
                try:
                    raw = await item.read(config.MAX_BYTES + 1)
                    def save_import():
                        with import_lock:
                            for row, duplicate in importer.add(name, raw, mode):
                                if not duplicate:
                                    enqueue(row["id"])
                                results.append({"id": row["id"], "duplicate": duplicate, "name": row["original_name"]})
                    await asyncio.to_thread(save_import)
                except ValueError as exc:
                    errors.append({"name": name, "message": str(exc), "code": getattr(exc, "code", "")})
                except (OSError, sqlite3.OperationalError):
                    errors.append({"name": name, "message": "保存できませんでした。保存先の空き容量と書き込み権限を確認して、もう一度取り込んでください。保存済みの原本は保持しています。"})
            return {"imported": results, "errors": errors}
        finally:
            await form.close()

    class RetryBatchRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        rows: list[ReviewRow] = Field(min_length=1, max_length=500)

    @app.post("/api/processing/retry")
    def retry_batch(body: RetryBatchRequest):
        retried, issues, seen = [], [], set()
        for selected in body.rows:
            if selected.id in seen:
                continue
            seen.add(selected.id)
            try:
                current = store.get(selected.id)
                if current["status"] != "error" and not (current["status"] == "review" and current["error"]):
                    raise Conflict("読み取りに失敗した明細を選んでください")
                store.set_status(selected.id, selected.version, "retry")
                enqueue(selected.id)
                retried.append(selected.id)
            except (Conflict, Missing, ValueError) as exc:
                issues.append({"id": selected.id, "message": str(exc) if not isinstance(exc, Missing) else "明細が見つかりません"})
        return {"retried": retried, "issues": issues}

    class ExcludeBatchRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        rows: list[ReviewRow] = Field(min_length=1, max_length=500)
        action: Literal["exclude", "restore"] = "exclude"

    @app.post("/api/ledger/status")
    def ledger_status(body: ExcludeBatchRequest):
        changed, issues, seen = [], [], set()
        for selected in body.rows:
            if selected.id in seen:
                continue
            seen.add(selected.id)
            try:
                if body.action == "restore" and store.get(selected.id)["status"] != "excluded":
                    raise Conflict("対象外の明細だけを選んでください")
                store.set_status(selected.id, selected.version, body.action)
                changed.append(selected.id)
            except (Conflict, Missing, ValueError) as exc:
                issues.append({"id": selected.id, "message": str(exc) if not isinstance(exc, Missing) else "明細が見つかりません"})
        return {"changed": changed, "issues": issues}

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

    @app.get("/api/receipts/{receipt_id}/source")
    def original_source(receipt_id: str, source: str = ""):
        store.get(receipt_id)
        document = importer.source(receipt_id, source or None)
        if document is None:
            raise HTTPException(404, "この明細はページ分割で取り込まれていません")
        path = importer.source_path(document["sha256"])
        if not path.is_file():
            raise HTTPException(404, "元PDFが見つかりません。バックアップを確認してください")
        return FileResponse(path, filename=document["name"])

    @app.get("/api/receipts/{receipt_id}/preview/{page}")
    def preview(receipt_id: str, page: int):
        if not 0 <= page < config.MAX_PAGES:
            raise HTTPException(404)
        row = store.get(receipt_id)
        path = ensure_preview(store, row, page)
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/api/receipts/{receipt_id}/history")
    def history(receipt_id: str):
        rows = store.history(receipt_id)
        for row in rows:
            row.pop("detail_json", None)
            if row["action"] == "analysis":
                detail = row["detail"]
                ocr = detail.get("ocr") or {}
                suggestion = detail.get("suggestion") or {}
                row["detail"] = {"model": detail.get("model"), "error": detail.get("error", ""),
                                 "ocr_summary": {"page_count": ocr.get("page_count"), "seconds": ocr.get("seconds")},
                                 "suggestion_summary": {k: suggestion.get(k) for k in ("date", "merchant", "total", "account")}}
        return {"history": rows}

    def check_month(month):
        import re
        if month and not re.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])", month):
            raise HTTPException(422, "年月の指定が正しくありません")

    @app.get("/api/summary")
    def totals(month: str = ""):
        check_month(month)
        return summary(list_summaries(store), month)

    @app.get("/api/export-preview")
    def export_preview(profile:str='generic',kind:str='journal',month:str='',tax_mode:str='reviewed',new_only:bool=False):
        check_month(month)
        rows, history = export_history.filter(list_summaries(store),profile,kind,new_only)
        result = build_export(rows,profile,month,kind,tax_mode,store.export_map(profile)).preview()
        duplicate_count = len(possible_duplicates(rows))
        if duplicate_count:
            result["warnings"].append(f"同じ日付・支払先・金額の明細が{duplicate_count}件あります。重複でないか原本を確認してください。自動では削除しません。")
        return {**result,"csv_history":history}

    class ExportMapRequest(BaseModel):
        model_config=ConfigDict(extra='forbid')
        profile:Literal['freee','mf','yayoi']
        names:dict[str,str]=Field(max_length=100)

    @app.post('/api/export-map')
    def export_map(body:ExportMapRequest):
        return {'names':store.export_map(body.profile,body.names)}

    @app.get("/api/export/{kind}")
    @app.post("/api/export/{kind}")
    def export(kind: str, request: Request, month: str = "", profile:str='generic',tax_mode:str='reviewed',new_only:bool=False):
        check_month(month)
        if kind not in ["expenses", "journal", "review"]:
            raise HTTPException(404)
        rows, _ = export_history.filter(list_summaries(store),profile,kind,new_only)
        result=build_export(rows,profile,month,kind,tax_mode,store.export_map(profile))
        content=result.content()
        if request.method == "POST":
            export_history.record(profile,kind,month,content,result,rows)
        encoding='shift_jis' if result.encoding=='cp932' else 'utf-8'
        return Response(content, media_type=f"text/csv; charset={encoding}", headers={"Content-Disposition": f'attachment; filename="{profile}-{kind}-{month or "all"}.csv"'})

    @app.get("/api/backup")
    def backup():
        temporary, archive = create_backup(store, importer)
        return FileResponse(archive, media_type="application/zip", filename="ryoshu-backup.zip", background=BackgroundTask(temporary.cleanup))

    app.mount("/evidence", StaticFiles(directory=config.APP_DIR / "docs/testing", html=True, check_dir=False), name="evidence")
    app.mount("/samples", StaticFiles(directory=config.APP_DIR / "samples", check_dir=False), name="samples")
    app.mount("/", StaticFiles(directory=config.APP_DIR / "web", html=True), name="web")
    return app
