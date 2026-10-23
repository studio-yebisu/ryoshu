import asyncio
import importlib.metadata
import json
import os
import sqlcipher3 as sqlite3
import time
from urllib.parse import quote
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
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool
from fastapi.exceptions import RequestValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__, config
from .accounting import amounts, csv_export, journal, summary
from .catalog import account_categories
from .exports import PROFILES, TAX_CHOICES, build_export
from .pipeline import local_models, process, validate_file, ModelSafetyError
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
from .protection import ProtectionError
from .session import Sessions
from .upload_limit import MemoryMultipartParser


def create_app(data_dir=None, run_workers=True):
    if config.MAX_REQUEST_BYTES>=MemoryMultipartParser.spool_max_size:
        raise RuntimeError("取り込みサイズがメモリ内の上限を超えています。設定を確認してください。")
    store = Store(Path(data_dir or config.DATA_DIR))
    importer = Importer(store) if store.ready else None
    export_history = ExportHistory(store) if store.ready else None
    sessions = Sessions(store.root, config.PORT)
    setup_lock = threading.Lock()
    retry_after = 0
    upload_slots = asyncio.Semaphore(2)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="receipt")
    import_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        sessions.publish()
        if store.ready and run_workers:
            for receipt_id in recover_queue(store):
                enqueue(receipt_id)
        elif store.ready:
            store.recover()
        yield
        executor.shutdown(wait=True, cancel_futures=True)

    app = FastAPI(title="Ryoshu", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.store = store
    app.state.sessions = sessions
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.add_middleware(UploadSizeLimit, max_bytes=config.MAX_REQUEST_BYTES)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in {"/api/health", "/api/session"}:
            if not sessions.authorized(request.headers.get("authorization")):
                return JSONResponse({"detail":"起動リンクからアプリを開いてください。「Ryoshu.cmd」をもう一度起動すると開けます。"}, status_code=401,
                                    headers={"Cache-Control":"no-store"})
            if (not store.ready or importer is None or export_history is None) and not path.startswith("/api/security"):
                return JSONResponse({"detail":"先に帳簿の保護を設定するか、復元用パスワードで開いてください。"}, status_code=423,
                                    headers={"Cache-Control":"no-store"})
        if request.url.path.startswith("/api/") and request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "このアプリの画面から操作してください"}, status_code=403)
        if request.url.path.startswith("/api/") and request.method not in ["GET", "HEAD"]:
            origin = request.headers.get("origin")
            allowed = {str(request.base_url).rstrip("/")}
            if request.headers.get("x-ledger-request") != "1" or (origin and origin not in allowed) or request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "このアプリの画面から操作してください"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; frame-src 'self' blob:; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
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
    def health(request: Request):
        public = {"app":"receipt-ledger", "app_version":__version__, "instance":sessions.instance}
        if not sessions.authorized(request.headers.get("authorization")):
            return public
        model_issue=""
        try:
            models = local_models()
            installed = any(m.get("name") == config.MODEL for m in models)
            ollama = True
        except Exception as exc:
            installed = ollama = False
            if isinstance(exc,ModelSafetyError):model_issue=str(exc)
        return {"app": "receipt-ledger", "app_version": __version__, "ocr_version": importlib.metadata.version("indra-ocr"), "model": config.MODEL,
                "environment_label":os.environ.get('RECEIPT_LEDGER_ENV_LABEL',''), "source_dir": str(Path(__file__).resolve().parents[1]),
                "model_ready": installed, "ollama_ready": ollama,"model_issue":model_issue, "compute": "CPU", "data_dir": str(store.root),
                "schema_version":3,"account_count":len(store.accounts(False)) if store.ready else 0,"policy_count":len(store.policies()) if store.ready else 0,
                "instance":sessions.instance,"protected":store.ready,"security_mode":store.keys.mode,
                "privacy": "loopback-only; local OCR and local model; offline OCR cache"}

    @app.post("/api/session")
    def open_session(request: Request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bootstrap "):
            raise HTTPException(401, "Ryoshu.cmdから開き直してください。")
        try:
            return {"token":sessions.exchange(authorization[10:])}
        except ProtectionError as exc:
            raise HTTPException(401, str(exc)) from None

    @app.get("/api/security")
    def security_status():
        return {"ready":store.ready,"mode":store.keys.mode,"error":store.initialization_error,
                "can_retry":store.keys.mode=="unlocked",
                "migrate_legacy":(store.root / "ledger.sqlite3").is_file() and not (store.root / "ledger.enc.sqlite3").is_file()}

    class ProtectionRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        password: SecretStr = Field(min_length=12,max_length=256)
        confirmation: SecretStr | None = Field(default=None,min_length=12,max_length=256)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Pydantic's default response includes the raw input, including passwords.
        errors=[{k:v for k,v in item.items() if k in {"loc","msg","type"}} for item in exc.errors()]
        return JSONResponse({"detail":errors},status_code=422)

    @app.post("/api/security/open")
    def protection_setup(body: ProtectionRequest):
        nonlocal importer, export_history, retry_after
        if store.ready:
            raise HTTPException(409,"この帳簿は保護されています。")
        if time.monotonic() < retry_after or not setup_lock.acquire(blocking=False):
            raise HTTPException(429,"少し待ってから、もう一度お試しください。")
        try:
            password = body.password.get_secret_value()
            if store.keys.mode == "new":
                if body.confirmation is None or body.confirmation.get_secret_value() != password:
                    raise ProtectionError("確認用のパスワードが一致しません。")
                store.keys.setup(password)
            else:
                def validate(master, identity):
                    from .database import CipherDatabase
                    from .protection import LedgerKeys
                    candidate = LedgerKeys.__new__(LedgerKeys)
                    candidate.root, candidate.master, candidate.ledger_id, candidate.mode = store.root, master, identity, "unlocked"
                    helper = CipherDatabase.__new__(CipherDatabase)
                    helper.keys, helper.path = candidate, store.path
                    if store.path.exists():
                        with closing(helper.open(readonly=True)) as db:
                            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                                raise ProtectionError("帳簿の整合性を確認できません。")
                store.keys.recover(password, validate)
            store.initialize()
            importer, export_history = Importer(store), ExportHistory(store)
            for receipt_id in recover_queue(store) if run_workers else []:
                enqueue(receipt_id)
            return {"ready":True}
        except ProtectionError as exc:
            store.initialization_error=str(exc)
            retry_after = time.monotonic() + 3
            raise
        finally:
            setup_lock.release()

    @app.post("/api/security/retry")
    def retry_initialization():
        nonlocal importer, export_history
        if not setup_lock.acquire(blocking=False):
            raise HTTPException(429,"帳簿を確認しています。少し待ってからやり直してください。")
        try:
            store.initialize()
            importer,export_history=Importer(store),ExportHistory(store)
            for receipt_id in recover_queue(store) if run_workers else []:
                enqueue(receipt_id)
            return {"ready":True}
        except ProtectionError as exc:
            store.initialization_error=str(exc)
            raise
        finally:
            setup_lock.release()

    @app.post("/api/security/password")
    def change_recovery_password(body: ProtectionRequest):
        nonlocal retry_after
        if not store.ready:
            raise HTTPException(423,"帳簿を開いてから変更してください。")
        if time.monotonic()<retry_after or not setup_lock.acquire(blocking=False):
            raise HTTPException(429,"少し待ってから、もう一度お試しください。")
        try:
            password=body.password.get_secret_value()
            if body.confirmation is None or body.confirmation.get_secret_value()!=password:
                raise ProtectionError("確認用のパスワードが一致しません。")
            store.keys.reset_password(password)
            return {"changed":True,"message":"復元用パスワードを変更しました。新しいバックアップを保存してください。以前のバックアップには以前のパスワードが必要です。"}
        finally:
            retry_after=time.monotonic()+3
            setup_lock.release()

    @app.get("/api/config")
    def app_config():
        return {"accounts": store.accounts(), "account_categories": account_categories(), "favorite_accounts": store.favorite_accounts(), "payments": config.PAYMENTS, "tax_rates": config.TAX_LABELS,
                "business_context": store.business_context(), "rules": store.rules(),
                "review_before_ledger": review_before_ledger(store),
                "storage_issues":store.storage_issues(),
                "export_tax_mode": export_tax_mode(store),
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
        category_id:str | None=Field(default=None,max_length=100)
        description:str=Field(default="",max_length=1000)
        aliases:list[str]=Field(default_factory=list,max_length=10)
        review_questions:list[str]=Field(default_factory=list,max_length=10)
        keywords:list[str]=Field(default_factory=list,max_length=20)

    @app.post("/api/accounts")
    def add_account(body:AccountRequest):
        if any(len(s)>200 for s in body.aliases+body.review_questions+body.keywords):
            raise ValueError("別名・確認事項は各200文字以内で入力してください")
        return store.add_account(body.name,body.kind,body.description,body.aliases,body.review_questions,body.keywords,body.category_id)

    class FavoriteAccountRequest(BaseModel):
        model_config = ConfigDict(extra='forbid')
        favorite: StrictBool

    @app.post('/api/accounts/{code}/favorite')
    def favorite_account(code: str, body: FavoriteAccountRequest):
        return {'favorite_accounts': store.set_account_favorite(code, body.favorite)}

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
        # Bounded RAM avoids plaintext multipart spooling into the OS temp directory.
        try:
            async with upload_slots:
                parser = MemoryMultipartParser(request.headers, request.stream(), max_files=config.MAX_FILES, max_fields=5, max_part_size=config.MAX_BYTES)
                form = await parser.parse()
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
        raw = store.keys.read_file(store.folder(receipt_id) / ("original" + row["extension"]))
        return Response(raw, media_type="application/pdf" if row["extension"] == ".pdf" else "application/octet-stream",
                        headers={"Content-Disposition":"attachment; filename*=utf-8''" + quote(row["original_name"])})

    @app.get("/api/receipts/{receipt_id}/source")
    def original_source(receipt_id: str, source: str = ""):
        store.get(receipt_id)
        document = importer.source(receipt_id, source or None)
        if document is None:
            raise HTTPException(404, "この明細はページ分割で取り込まれていません")
        path = importer.source_path(document["sha256"])
        if not path.is_file():
            raise HTTPException(404, "元PDFが見つかりません。バックアップを確認してください")
        return Response(store.keys.read_file(path), media_type="application/pdf",
                        headers={"Content-Disposition":"attachment; filename*=utf-8''" + quote(document["name"])})

    @app.get("/api/receipts/{receipt_id}/preview/{page}")
    def preview(receipt_id: str, page: int):
        if not 0 <= page < config.MAX_PAGES:
            raise HTTPException(404)
        row = store.get(receipt_id)
        path = ensure_preview(store, row, page)
        return Response(store.keys.read_file(path), media_type="image/jpeg")

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

    class BackupRequest(ProtectionRequest):
        allow_missing:bool=Field(default=False,strict=True)

    @app.post("/api/backup")
    def backup(body: BackupRequest):
        nonlocal retry_after
        if time.monotonic()<retry_after or not setup_lock.acquire(blocking=False):
            raise HTTPException(429,"少し待ってから、もう一度お試しください。")
        try:
            temporary,archive=create_backup(store,importer,body.password.get_secret_value(),allow_missing=body.allow_missing)
            return FileResponse(archive,media_type="application/octet-stream",filename="ryoshu-backup-partial.ryoshubak" if body.allow_missing else "ryoshu-backup.ryoshubak",background=BackgroundTask(temporary.cleanup))
        finally:
            retry_after=time.monotonic()+3
            setup_lock.release()

    app.mount("/evidence", StaticFiles(directory=config.APP_DIR / "docs/testing", html=True, check_dir=False), name="evidence")
    app.mount("/samples", StaticFiles(directory=config.APP_DIR / "samples", check_dir=False), name="samples")
    app.mount("/", StaticFiles(directory=config.APP_DIR / "web", html=True), name="web")
    return app
