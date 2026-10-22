from support import PASSWORD, backup_files
import concurrent.futures
import hashlib
import io
import json
from pathlib import Path
import threading
import zipfile

from support import LedgerClient as TestClient
from PIL import Image
import pytest
import pypdfium2 as pdfium

from receipt_ledger.importing import Importer
from receipt_ledger.pdf_runtime import PDF_LOCK
from receipt_ledger.pipeline import validate_file
from receipt_ledger.restore import restore_backup
from receipt_ledger.schemas import Draft
from support import ready_app as create_app
from support import ready_store as Store

HEADERS = {"X-Ledger-Request": "1"}


def pdf(count=2):
    with PDF_LOCK, pdfium.PdfDocument.new() as doc:
        for index in range(count):
            doc.new_page(200 + index, 300).close()
        output = io.BytesIO()
        doc.save(output)
        return output.getvalue()


def image(width=30, height=30):
    output = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(output, "PNG")
    return output.getvalue()


def analyzed(store, raw=b"fictional", **changes):
    row, _ = store.add("架空.png", ".png", raw)
    store.complete(row["id"], {"text": "架空文具店 用紙 合計1650円", "page_count": 1, "pages": [{"text": "x" * 100_000}]},
                   {"date": "2023-11-02", "merchant": "架空文具店", "total": 1650, "account": "消耗品費", "description": "用紙", **changes})
    return store.get(row["id"])


def test_csv_get_is_read_only_and_post_creation_rejects_cross_site_requests(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        row = analyzed(app.state.store)
        draft = Draft(**{**row["draft"], "purpose": "業務用紙", "payment": "owner", "business_percent": 100})
        app.state.store.save(row["id"], row["version"], draft, confirm=True)
        endpoint = "/api/export/journal?profile=freee&tax_mode=exempt"
        client.get(endpoint).raise_for_status()
        assert client.post(endpoint).status_code == 403
        assert client.post(endpoint, headers={**HEADERS, "Origin": "https://example.invalid"}).status_code == 403
        assert client.get(endpoint, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        with app.state.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM csv_exports").fetchone()[0] == 0
        client.post(endpoint, headers=HEADERS).raise_for_status()
        with app.state.store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM csv_exports").fetchone()[0] == 1


@pytest.mark.parametrize("width,height", [(40, 30), (60, 40)])
def test_image_pixel_warning_and_error_both_explain_how_to_resize(tmp_path, monkeypatch, width, height):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        result = client.post("/api/receipts", headers=HEADERS, files={"files": ("大きい.png", image(width, height))}).json()
        assert not result["imported"]
        assert "2500万画素以下に縮小" in result["errors"][0]["message"]


def test_batch_does_not_overwrite_explicit_zero_business_use(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        row = analyzed(app.state.store, business_percent=0)
        result = client.post("/api/ledger/review", headers=HEADERS, json={"rows": [{"id": row["id"], "version": row["version"]}],
                            "purpose": "共通用途", "business_percent": 100, "payment": "owner"}).json()
        assert not result["reviewed"] and result["issues"]
        assert app.state.store.get(row["id"])["draft"]["business_percent"] == 0


def test_unknown_total_does_not_become_zero_business_amount_in_api(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        row = analyzed(app.state.store, total=None, business_percent=100)
        result = client.get(f'/api/receipts/{row["id"]}').json()
        assert result["draft"]["total"] is None
        assert result["business_amount"] is None and result["private_amount"] is None


def test_restored_pdf_and_image_have_previews_without_ocr_or_ai(tmp_path, monkeypatch):
    app = create_app(tmp_path / "original", run_workers=False)
    with TestClient(app) as client:
        a, _ = app.state.store.add("架空.pdf", ".pdf", pdf())
        app.state.store.complete(a["id"], {"text": "架空", "page_count": 2}, None)
        b = analyzed(app.state.store, image())
        backup = tmp_path / "backup.zip"
        backup.write_bytes(client.post("/api/backup",headers={"X-Ledger-Request":"1"},json={"password":PASSWORD}).content)
    restored = restore_backup(backup, tmp_path / "restored", PASSWORD)
    app = create_app(restored["restored_data"], run_workers=False)
    def unexpected(*args):
        raise AssertionError("Preview must not run OCR or AI")
    monkeypatch.setattr("receipt_ledger.server.process", unexpected)
    with TestClient(app) as client:
        assert client.get(f'/api/receipts/{a["id"]}').json()["preview_count"] == 2
        for row, page in [(a, 0), (a, 1), (b, 0)]:
            result = client.get(f'/api/receipts/{row["id"]}/preview/{page}')
            result.raise_for_status()
            with Image.open(io.BytesIO(result.content)) as decoded:
                assert decoded.format == "JPEG"
        assert client.get(f'/api/receipts/{a["id"]}/original').content == pdf_bytes(app.state.store, a["id"])


def pdf_bytes(store, receipt_id):
    row = store.get(receipt_id)
    return store.keys.read_file(store.folder(receipt_id) / ("original" + row["extension"]))


def test_same_page_from_different_source_pdfs_keeps_both_originals_in_backup(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    store = app.state.store
    importer = Importer(store)
    source_a, source_b = pdf(2), pdf(3)
    first = list(importer.add("元A.pdf", source_a, "pages"))[0][0]
    digest_b = hashlib.sha256(source_b).hexdigest()
    cache = importer.source_path(digest_b).parent
    cache.mkdir(parents=True)
    store.keys.write_file(cache / "page-0001.pdf",pdf_bytes(store, first["id"]))
    rows = list(importer.add("元B.pdf", source_b, "pages"))
    assert rows[0][0]["id"] == first["id"] and rows[0][1]
    assert len(importer.sources(first["id"])) == 2
    with TestClient(app) as client:
        listed = client.get("/api/receipts").json()["receipts"]
        assert len(listed) == len({row["id"] for row in listed})
        assert client.get(f'/api/receipts/{first["id"]}/source?source={digest_b}').content == source_b
        result = client.post("/api/backup",headers={"X-Ledger-Request":"1"},json={"password":PASSWORD})
        result.raise_for_status()
        files = backup_files(result.content)
        assert files[f"data/imports/{digest_b}/source.pdf"] == source_b
        assert files[f"data/imports/{hashlib.sha256(source_a).hexdigest()}/source.pdf"] == source_a


def test_source_mapping_migration_preserves_rows_and_saves_snapshot(tmp_path):
    store = Store(tmp_path)
    row, _ = store.add("架空.pdf", ".pdf", pdf())
    with store.connect() as db:
        db.execute("CREATE TABLE receipt_import_sources(receipt_id TEXT PRIMARY KEY REFERENCES receipts(id),source_sha256 TEXT NOT NULL,source_name TEXT NOT NULL,page INTEGER NOT NULL,page_count INTEGER NOT NULL)")
        db.execute("INSERT INTO receipt_import_sources VALUES(?,?,?,?,?)", (row["id"], row["sha256"], "架空.pdf", 1, 2))
    assert Importer(store).source(row["id"])["sha256"] == row["sha256"]
    assert list((tmp_path / "backups").glob("before-source-provenance-v2-*.sqlite3"))


def test_large_history_response_is_compact_but_saved_evidence_is_untouched(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        row = analyzed(app.state.store)
        result = client.get(f'/api/receipts/{row["id"]}/history')
        assert len(result.content) < 10_000
        assert "ocr_summary" in result.json()["history"][0]["detail"]
        with app.state.store.connect() as db:
            raw = db.execute("SELECT detail_json FROM audit WHERE receipt_id=? AND action='analysis'", (row["id"],)).fetchone()[0]
            assert len(raw) > 100_000


def test_bulk_exclude_restore_is_versioned_and_preserves_originals(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        row = analyzed(app.state.store, image())
        stale = {"id": row["id"], "version": row["version"] - 1}
        assert client.post("/api/ledger/status", headers=HEADERS, json={"rows": [stale]}).json()["issues"]
        result = client.post("/api/ledger/status", headers=HEADERS, json={"rows": [{"id": row["id"], "version": row["version"]}]}).json()
        assert result["changed"] == [row["id"]]
        changed = app.state.store.get(row["id"])
        result = client.post("/api/ledger/status", headers=HEADERS, json={"action": "restore", "rows": [{"id": row["id"], "version": changed["version"]}]}).json()
        assert result["changed"] == [row["id"]]
        assert app.state.store.get(row["id"])["status"] == "review"
        assert pdf_bytes(app.state.store, row["id"]) == image()


def test_missing_original_backup_reports_actionable_message(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        row = analyzed(app.state.store)
        (app.state.store.folder(row["id"]) / "original.png").unlink()
        result = client.post("/api/backup",headers={"X-Ledger-Request":"1"},json={"password":PASSWORD})
        assert result.status_code == 422 and "原本が見つかりません" in result.json()["detail"]


def test_pdf_validation_waits_for_process_wide_pdfium_lock():
    raw = pdf()
    started, done = threading.Event(), threading.Event()
    def validate():
        started.set()
        validate_file(raw)
        done.set()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        with PDF_LOCK:
            future = executor.submit(validate)
            assert started.wait(1)
            assert not done.wait(.03)
        future.result(timeout=3)
    assert done.is_set()


def test_image_validation_does_not_wait_for_pdf_ocr_lock():
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        with PDF_LOCK:
            future = executor.submit(validate_file, image())
            assert future.result(timeout=2) == ".png"


def test_duplicate_warning_payload_is_bounded_for_a_large_identical_group():
    from receipt_ledger.listing import possible_duplicates
    rows = [{"id": f"{i:032x}", "status": "review", "draft": {"date": "2023-11-02", "total": 1000, "merchant": "架空店"}} for i in range(2000)]
    warnings = possible_duplicates(rows)
    assert len(warnings) == 2000
    assert all(0 < len(ids) <= 10 and receipt_id not in ids for receipt_id, ids in warnings.items())


@pytest.mark.parametrize("size,accepted", [((4000, 3500), True), ((5200, 5000), False)])
def test_real_pixel_limit_matches_the_25_megapixel_guidance(size, accepted):
    buffer = io.BytesIO()
    with Image.new("RGB", size, "white") as source:
        source.save(buffer, "PNG")
    if accepted:
        assert validate_file(buffer.getvalue()) == ".png"
    else:
        with pytest.raises(ValueError, match="2500万画素"):
            validate_file(buffer.getvalue())


def test_single_restore_does_not_demote_a_confirmed_posting(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    row = analyzed(app.state.store)
    draft = Draft(**row["draft"]).model_copy(update={"purpose": "架空の制作業務", "payment": "owner", "business_percent": 100})
    saved = app.state.store.save(row["id"], row["version"], draft, confirm=True)
    with TestClient(app) as client:
        response = client.post(f'/api/receipts/{row["id"]}/restore', headers=HEADERS, json={"version": saved["version"]})
        assert response.status_code == 409
        assert app.state.store.get(row["id"])["status"] == "confirmed"
