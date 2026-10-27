from support import PASSWORD, backup_files
import concurrent.futures
import hashlib
import io
import json
import threading
import zipfile

from support import LedgerClient as TestClient
import pypdfium2 as pdfium
from PIL import Image

from receipt_ledger import config
from support import ready_app as create_app
from support import ready_store as Store
from receipt_ledger.workflow import recover_queue

HEADERS = {"X-Ledger-Request": "1"}


def pdf(pages=2):
    with pdfium.PdfDocument.new() as doc:
        for index in range(pages):
            doc.new_page(200 + index, 300).close()
        output = io.BytesIO()
        doc.save(output)
        return output.getvalue()


def png():
    output = io.BytesIO()
    Image.new("RGB", (30, 30), "white").save(output, "PNG")
    return output.getvalue()


def upload(client, raw, mode="pages", name="架空まとめ.pdf"):
    return client.post("/api/receipts", headers=HEADERS,
                       files={"files": (name, raw)}, data={"pdf_mode": mode})


def test_page_split_preserves_source_provenance_and_duplicate_identity_after_restore(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    raw = pdf(12)
    with TestClient(app) as client:
        result = upload(client, raw).json()
        assert not result["errors"] and len(result["imported"]) == 12
        first = result["imported"][0]["id"]
        row = client.get(f"/api/receipts/{first}").json()
        assert row["source_document"]["page"] == 1
        assert row["source_document"]["page_count"] == 12
        assert client.get(f"/api/receipts/{first}/source").content == raw
        original = client.get(f"/api/receipts/{first}/original").content
        with pdfium.PdfDocument(original) as page:
            assert len(page) == 1
        bundle = client.post("/api/backup",headers={"X-Ledger-Request":"1"},json={"password":PASSWORD})
        bundle.raise_for_status()
        files = backup_files(bundle.content)
        assert files[f"data/imports/{hashlib.sha256(raw).hexdigest()}/source.pdf"] == raw
        from receipt_ledger.restore import restore_backup
        backup_path = tmp_path / 'test.ryoshubak'
        backup_path.write_bytes(bundle.content)
        restored = tmp_path / "restored"
        restore_backup(backup_path,restored,PASSWORD)
        restored_app = create_app(restored / "data", run_workers=False)
        with TestClient(restored_app) as other:
            repeated = upload(other, raw).json()
            assert len(repeated["imported"]) == 12
            assert all(r["duplicate"] for r in repeated["imported"])
            assert len(other.get("/api/receipts").json()["receipts"]) == 12
        assert upload(client, raw, "document").json()["errors"]


def test_single_document_mode_does_not_split_and_bad_files_do_not_discard_good_ones(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        result = client.post("/api/receipts", headers=HEADERS, data={"pdf_mode": "document"}, files=[
            ("files", ("架空.pdf", pdf(3))), ("files", ("壊れた.pdf", b"%PDF-invalid")), ("files", ("画像.png", png()))
        ]).json()
        assert len(result["imported"]) == 2 and len(result["errors"]) == 1
        row = client.get("/api/receipts/" + result["imported"][0]["id"]).json()
        assert row["source_document"] is None
        with pdfium.PdfDocument(client.get(f'/api/receipts/{row["id"]}/original').content) as doc:
            assert len(doc) == 3
        assert client.get(f'/api/receipts/{row["id"]}/source').status_code == 404


def test_split_limits_and_invalid_mode_leave_no_receipts(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_SPLIT_PAGES", 2)
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        assert upload(client, pdf(3)).json()["errors"]
        assert upload(client, pdf(1), "anything").status_code == 400
        assert not client.get("/api/receipts").json()["receipts"]


def test_multipart_body_limit_is_streamed_and_keeps_existing_records(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_REQUEST_BYTES", 1024)
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        assert upload(client, png(), "document", "画像.png").json()["imported"]
        result = upload(client, b"x" * 1500, "document")
        assert result.status_code == 413
        assert len(client.get("/api/receipts").json()["receipts"]) == 1


def test_simultaneous_duplicate_uploads_save_one_original(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    raw = pdf(2)
    with TestClient(app) as client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: upload(client, raw).json(), range(4)))
        assert all(not r["errors"] for r in results)
        assert sum(not i["duplicate"] for r in results for i in r["imported"]) == 2
        assert len(client.get("/api/receipts").json()["receipts"]) == 2


def test_interrupted_processing_restarts_automatically_without_losing_draft(tmp_path, monkeypatch):
    store = Store(tmp_path)
    row, _ = store.add("架空.png", ".png", png())
    store.stage(row["id"], "ocr")
    finished = threading.Event()
    def process(saved, receipt_id, runtime=None):
        saved.complete(receipt_id, {"text": "再開の検査"}, None)
        finished.set()
    monkeypatch.setattr("receipt_ledger.server.process", process)
    app = create_app(tmp_path)
    with TestClient(app):
        assert finished.wait(3)
        assert store.get(row["id"])["status"] == "review"
        assert store.keys.read_file(store.folder(row["id"]) / "original.png") == png()


def test_batch_retry_is_versioned_preserves_inputs_and_does_not_touch_confirmed(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    store = app.state.store
    with TestClient(app) as client:
        row, _ = store.add("架空.png", ".png", png())
        store.complete(row["id"], None, None, "前回失敗")
        row = store.get(row["id"])
        stale = {"id": row["id"], "version": row["version"] - 1}
        assert client.post("/api/processing/retry", headers=HEADERS, json={"rows": [stale]}).json()["issues"]
        current = {"id": row["id"], "version": row["version"]}
        result = client.post("/api/processing/retry", headers=HEADERS, json={"rows": [current, current]}).json()
        assert result["retried"] == [row["id"]]
        assert store.get(row["id"])["draft"] == row["draft"]


def test_repeatedly_interrupted_processing_does_not_auto_restart_forever(tmp_path):
    store = Store(tmp_path)
    row, _ = store.add("架空.png", ".png", png())
    store.stage(row["id"], "ocr")
    assert recover_queue(store) == [row["id"]]
    store.stage(row["id"], "ocr")
    assert recover_queue(store) == []
    current = store.get(row["id"])
    assert current["status"] == "error"
    store.set_status(row["id"], current["version"], "retry")
    store.stage(row["id"], "ocr")
    assert recover_queue(store) == [row["id"]]


def test_oversize_file_does_not_discard_other_files_in_same_request(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        result = client.post("/api/receipts", headers=HEADERS, files=[
            ("files", ("大きすぎる.pdf", b"%PDF-" + b"x" * config.MAX_BYTES)),
            ("files", ("正常.png", png()))
        ]).json()
        assert len(result["imported"]) == 1 and len(result["errors"]) == 1
        assert "20MB" in result["errors"][0]["message"]


def test_single_page_pdf_is_not_duplicated_when_import_mode_changes(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        raw = pdf(1)
        first = upload(client, raw, mode="pages").json()["imported"][0]
        second = upload(client, raw, mode="document").json()["imported"][0]
        assert first["id"] == second["id"] and second["duplicate"]
        assert client.get(f'/api/receipts/{first["id"]}/original').content == raw
        assert len(client.get("/api/receipts").json()["receipts"]) == 1


def test_native_split_failure_keeps_saved_pages_and_imports_other_files(tmp_path, monkeypatch):
    raw = pdf(3)
    original_import = pdfium.PdfDocument.import_pages
    def broken_page(document, source, pages, *args, **kwargs):
        if pages == [1]:
            raise pdfium.PdfiumError("injected native page failure")
        return original_import(document, source, pages, *args, **kwargs)
    monkeypatch.setattr(pdfium.PdfDocument, "import_pages", broken_page)
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        result = client.post("/api/receipts", headers=HEADERS,
                             files=[("files", ("一部が壊れた.pdf", raw)), ("files", ("正常.png", png()))],
                             data={"pdf_mode": "pages"})
        result.raise_for_status()
        body = result.json()
        assert len(body["imported"]) == 2 and len(body["errors"]) == 1
        assert "PDFの一部を分割できません" in body["errors"][0]["message"]
        first = body["imported"][0]["id"]
        assert client.get(f"/api/receipts/{first}/source").content == raw
        retry = upload(client, raw).json()
        assert retry["imported"][0]["id"] == first and retry["imported"][0]["duplicate"]
        assert len(client.get("/api/receipts").json()["receipts"]) == 2
