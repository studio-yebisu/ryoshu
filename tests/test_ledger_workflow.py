import csv
import io

from support import LedgerClient as TestClient

from receipt_ledger.schemas import Draft
from support import ready_app as create_app
from support import ready_store as Store
from receipt_ledger.workflow import review_before_ledger

HEADERS = {"X-Ledger-Request": "1"}


def analyzed(store, content=b"fictional", **changes):
    row, _ = store.add("架空.png", ".png", content)
    suggestion = {"date": "2023-11-02", "merchant": "文具店", "total": 1760,
                  "account": "消耗品費", "description": "用紙", **changes}
    store.complete(row["id"], {"text": "文具店 用紙 合計1,760円", "page_count": 1}, suggestion)
    return store.get(row["id"])


def test_ledger_is_created_before_review_and_survives_restart(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        assert client.get("/api/config").json()["review_before_ledger"] is False
        row = analyzed(app.state.store)
        ledger = client.get("/api/ledger").json()
        assert ledger["unreviewed_count"] == 1
        assert ledger["rows"][0]["draft"]["total"] == 1760
        assert ledger["rows"][0]["draft"]["business_percent"] is None
        assert ledger["rows"][0]["posting"] is None
        export = client.get("/api/export/review?profile=generic&kind=review").content
        assert "1760" in export.decode("utf-8-sig")
        assert client.get("/api/export-preview?profile=freee&tax_mode=exempt").json()["receipt_count"] == 0
    assert Store(tmp_path).get(row["id"])["status"] == "review"


def test_review_order_setting_persists_without_changing_existing_rows(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        row = analyzed(app.state.store)
        client.post("/api/settings", json={"business_context": "制作"}, headers=HEADERS).raise_for_status()
        client.post("/api/settings", json={"review_before_ledger": True}, headers=HEADERS).raise_for_status()
        assert client.get("/api/ledger").json()["rows"] == []
        assert client.get("/api/config").json()["business_context"] == "制作"
        assert app.state.store.get(row["id"])["status"] == "review"
    assert review_before_ledger(Store(tmp_path)) is True


def test_later_batch_review_posts_only_valid_rows_and_preserves_existing_inputs(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        store = app.state.store
        a = analyzed(store, b"one")
        a = store.save(a["id"], a["version"], Draft(**{**a["draft"], "business_percent": 50, "purpose": "既存の用途", "payment": "bank"}))
        b = analyzed(store, b"two", total=None)
        result = client.post("/api/ledger/review", headers=HEADERS, json={
            "rows": [{"id": r["id"], "version": r["version"]} for r in [a, b]],
            "business_percent": 100, "purpose": "新しい共通用途", "payment": "owner"}).json()
        assert result["reviewed"] == [a["id"]]
        assert result["issues"][0]["id"] == b["id"]
        saved = store.get(a["id"])
        assert saved["draft"]["purpose"] == "既存の用途"
        assert saved["draft"]["business_percent"] == 50
        assert saved["draft"]["payment"] == "bank"
        assert saved["posting"] and store.get(b["id"])["posting"] is None
        assert client.get("/api/ledger").json()["unreviewed_count"] == 1
        assert client.get("/api/export-preview?profile=freee&tax_mode=exempt").json()["receipt_count"] == 1


def test_stale_batch_review_cannot_overwrite_a_newer_edit(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        store = app.state.store
        row = analyzed(store)
        newer = store.save(row["id"], row["version"], Draft(**{**row["draft"], "total": 2000}))
        result = client.post("/api/ledger/review", headers=HEADERS, json={"rows": [{"id": row["id"], "version": row["version"]}], "business_percent": 100, "payment": "owner", "purpose": "制作"}).json()
        assert not result["reviewed"] and result["issues"]
        assert store.get(row["id"])["draft"]["total"] == 2000
        assert store.get(row["id"])["status"] == "review"
