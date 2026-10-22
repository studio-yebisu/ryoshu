from support import PASSWORD, backup_files
import csv
import hashlib
import io
import json
import zipfile

import pytest
from support import LedgerClient as TestClient
from PIL import Image

from receipt_ledger.accounting import amounts, csv_export, journal
from receipt_ledger.pipeline import dates_in, ground, validate_file
from receipt_ledger.schemas import Draft, confirmation_errors
from support import ready_app as create_app
from receipt_ledger.store import Conflict
from support import ready_store as Store


def draft(**changes):
    return Draft(date="2026-10-18", merchant="文具店", total=1650, account="消耗品費", payment="owner", business_percent=50,
                 description="用紙", purpose="仕事の資料作り", **changes)


def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (100, 100), "white").save(output, "PNG")
    return output.getvalue()


def add_ready(store):
    row, _ = store.add("架空.png", ".png", image_bytes())
    store.complete(row["id"], {"text": "合計 1650円", "pages": [], "page_count": 1}, None)
    return store.get(row["id"])


@pytest.mark.parametrize("payment,expected", [("owner", [("消耗品費", "事業主借", 825)]), ("bank", [("消耗品費", "普通預金", 825), ("事業主貸", "普通預金", 825)]), ("card", [("消耗品費", "未払金", 825), ("事業主貸", "未払金", 825)])])
def test_business_private_split_has_correct_counterpart(payment, expected):
    d = draft().model_dump()
    d["payment"] = payment
    assert [(r["debit"], r["credit"], r["amount"]) for r in journal(d)] == expected


def test_yen_rounding_is_decimal_half_up():
    assert amounts({"total": 101, "business_percent": 50}) == (51, 50)
    assert amounts({"total": 999_999_999, "business_percent": 99}) == (989_999_999, 10_000_000)


def test_no_assumption_about_business_share_or_payment():
    errors = confirmation_errors(Draft(date="2026-10-18", total=100, merchant="店", account="通信費"))
    assert any("割合" in e for e in errors)
    assert any("支払方法" in e for e in errors)
    assert any("用途" in e for e in errors)


def test_original_deduplication_and_stale_version(tmp_path):
    store = Store(tmp_path)
    row = add_ready(store)
    duplicate, is_duplicate = store.add("renamed.png", ".png", image_bytes())
    assert is_duplicate and duplicate["id"] == row["id"]
    saved = store.save(row["id"], row["version"], draft(), True, True)
    with pytest.raises(Conflict):
        store.save(row["id"], row["version"], draft())
    assert saved["status"] == "confirmed"
    assert len(store.rules()) == 1
    assert store.keys.read_file(store.folder(row["id"]) / "original.png") == image_bytes()
    assert store.history(row["id"])[0]["action"] == "confirm"


def test_reanalysis_preserves_manual_values_and_has_separate_suggestion(tmp_path):
    store = Store(tmp_path)
    row = add_ready(store)
    row = store.save(row["id"], row["version"], draft())
    store.complete(row["id"], {"text": "別のOCR"}, {"total": 888, "account": "雑費", "model": "test"})
    result = store.get(row["id"])
    assert result["draft"]["total"] == 1650
    assert result["suggestion"]["total"] == 888


def test_csv_excludes_unconfirmed_and_prevents_formula_injection(tmp_path):
    store = Store(tmp_path)
    row = add_ready(store)
    d = draft().model_copy(update={"merchant": "=HYPERLINK(1)", "description": "+formula"})
    store.save(row["id"], row["version"], d, confirm=True)
    content = csv_export(store.list())
    assert content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
    assert rows[1][1].startswith("'=") and rows[1][3].startswith("'+")
    assert len(rows) == 2
    latest = store.get(row["id"])
    store.set_status(row["id"], latest["version"], "exclude")
    assert len(csv_export(store.list()).decode("utf-8-sig").splitlines()) == 1


def candidate(**changes):
    return {"date": "2026-10-20", "date_evidence": "2026年10月20日", "merchant": "文具店", "merchant_evidence": "文具店", "total": 1650, "total_evidence": "合計 1,650円", "description": "用紙", "account": "消耗品費", "tax_rate": "10", "tax_amount": 150, "invoice_number": "", "confidence": "high", "reason": "文具", "warnings": [], **changes}


def test_model_hallucinations_and_tender_are_not_silently_accepted():
    text = "文具店\n2026年10月20日\n消費税(10%)150円\n合計 1,650円\nお預り 2,000円"
    good = ground(candidate(), text)
    assert good["total"] == 1650 and good["date"] == "2026-10-20"
    wrong = candidate()
    wrong.update(total=2000, total_evidence="お預り 2,000円", date="2025-10-20", merchant="架空ストア")
    bad = ground(wrong, text)
    assert bad["total"] is None and bad["date"] == "2026-10-20" and bad["merchant"] == ""
    assert bad["confidence"] == "low"


def test_era_date_and_invalid_date():
    assert dates_in("令和8年10月20日") == {"2026-10-20"}
    assert dates_in("2026/02/30") == set()


def test_food_does_not_become_business_meeting_expense_without_purpose():
    raw = candidate()
    raw.update(account="会議費", reason="コーヒーは会議費", total=880, total_evidence="合計 880円")
    checked = ground(raw, "文具店\n2026年10月20日\nコーヒー 800円\n消費税(10%)80円\n合計 880円")
    assert checked["account"] == ""
    assert "利用目的" in checked["reason"]
    assert checked["confidence"] == "low"


def test_upload_validation_rejects_arbitrary_bytes():
    assert validate_file(image_bytes()) == ".png"
    with pytest.raises(ValueError):
        validate_file(b"<svg onload=alert(1)>")


def test_api_manual_confirmation_security_backup_and_export(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert client.post("/api/settings", json={"business_context": "x"}).status_code == 403
        headers = {"X-Ledger-Request": "1"}
        assert client.post("/api/settings", json={"business_context": "x"}, headers={**headers, "Origin": "https://evil.example"}).status_code == 403
        upload = client.post("/api/receipts", files={"files": ("架空.png", image_bytes(), "image/png")}, headers=headers).json()
        rid = upload["imported"][0]["id"]
        app.state.store.complete(rid, {"text": "合計1650円", "pages": [], "page_count": 1}, None)
        row = client.get(f"/api/receipts/{rid}").json()
        invalid = client.post(f"/api/receipts/{rid}/save", json={"version": row["version"], "draft": Draft().model_dump(), "confirm": True}, headers=headers)
        assert invalid.status_code == 422
        result = client.post(f"/api/receipts/{rid}/save", json={"version": row["version"], "draft": draft().model_dump(), "confirm": True}, headers=headers)
        assert result.status_code == 200
        assert result.json()["business_amount"] == 825
        assert client.get("/api/summary").json()["expense_total"] == 825
        assert b"825" in client.get("/api/export/journal").content
        backup = client.post("/api/backup",headers={"X-Ledger-Request":"1"},json={"password":PASSWORD})
        assert backup.status_code == 200
        files = backup_files(backup.content)
        assert "data/ledger.enc.sqlite3" in files
        assert not files["data/ledger.enc.sqlite3"].startswith(b"SQLite format 3")
        assert hashlib.sha256(files[f"data/receipts/{rid}/original.png"]).hexdigest() == row["sha256"]
        assert client.get("/api/receipts/not-an-id/original").status_code == 404
