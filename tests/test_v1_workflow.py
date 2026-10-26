from support import PASSWORD, backup_files
import io
from pathlib import Path

from support import LedgerClient as TestClient

from receipt_ledger.schemas import Draft
from support import ready_app as create_app

HEADERS = {"X-Ledger-Request": "1"}


def analyzed(store, content=b"one", **changes):
    row, _ = store.add("架空.png", ".png", content)
    store.complete(row["id"], {"text": "架空文具店 用紙 合計1650円", "pages": [{"text": "x" * 100_000}]},
                   {"date": "2023-11-02", "merchant": "架空文具店", "total": 1650, "account": "消耗品費", "description": "用紙", **changes})
    return store.get(row["id"])


def test_small_list_keeps_detail_ocr_and_flags_possible_duplicates_without_deleting(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        a = analyzed(app.state.store)
        b = analyzed(app.state.store, b"another photo", merchant="架空 文具店")
        response = client.get("/api/receipts")
        assert len(response.content) < 20_000
        rows = response.json()["receipts"]
        assert all(r["ocr"] is None for r in rows)
        assert rows[0]["possible_duplicate_ids"]
        assert len(client.get(f'/api/receipts/{a["id"]}').json()["ocr"]["pages"][0]["text"]) == 100_000
        assert len(rows) == 2


def test_batch_tax_fills_only_empty_and_mixed_rate_stays_pending(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    store = app.state.store
    with TestClient(app) as client:
        a, b = analyzed(store), analyzed(store, b"mixed", tax_rate="mixed")
        result = client.post("/api/ledger/review", headers=HEADERS, json={"rows": [{"id": r["id"], "version": r["version"]} for r in [a, b]],
                "purpose": "業務用紙", "business_percent": 100, "payment": "owner", "export_tax": "purchase10"}).json()
        assert result["reviewed"] == [a["id"]]
        assert "8%と10%が混在する明細には税区分を一括入力できません。原本を確認し、会計ソフトでの分割方法を個別に決めてください" in result["issues"][0]["message"]
        assert store.get(b["id"])["status"] == "review"
        assert store.get(a["id"])["draft"]["export_tax"] == "purchase10"


def test_csv_history_only_skips_exported_versions_and_does_not_claim_saas_import(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    store = app.state.store
    with TestClient(app) as client:
        row = analyzed(store)
        draft = Draft(**{**row["draft"], "purpose": "業務用紙", "payment": "owner", "business_percent": 100, "export_tax": "outside"})
        row = store.save(row["id"], row["version"], draft, confirm=True)
        query = "profile=freee&kind=journal&tax_mode=reviewed&new_only=true"
        assert client.get("/api/export-preview?" + query).json()["receipt_count"] == 1
        client.post("/api/export/journal?" + query, headers=HEADERS).raise_for_status()
        result = client.get("/api/export-preview?" + query).json()
        assert result["receipt_count"] == 0 and result["csv_history"]["last_created_at"]
        assert client.get("/api/export-preview?profile=mf&kind=journal&new_only=true").json()["receipt_count"] == 1
        edited = store.save(row["id"], row["version"], draft.model_copy(update={"description": "用紙を修正"}), confirm=True)
        assert edited["version"] > row["version"]
        assert client.get("/api/export-preview?" + query).json()["receipt_count"] == 1


def test_pending_csv_history_includes_unknown_dates_and_excluded_rows(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        row = analyzed(app.state.store, date="")
        app.state.store.set_status(row["id"], row["version"], "exclude")
        query = "profile=generic&kind=review&month=2023-11&new_only=true"
        assert client.get("/api/export-preview?" + query).json()["receipt_count"] == 1
        client.post("/api/export/review?" + query, headers=HEADERS).raise_for_status()
        assert client.get("/api/export-preview?" + query).json()["receipt_count"] == 0


def test_backup_streams_archive_and_database_and_bounds_each_original(tmp_path, monkeypatch):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        analyzed(app.state.store)
        def forbidden(self):
            raise AssertionError("Backup must stream files instead of Path.read_bytes")
        monkeypatch.setattr(Path, "read_bytes", forbidden)
        result = client.post("/api/backup",headers={"X-Ledger-Request":"1"},json={"password":PASSWORD})
        result.raise_for_status()
        from receipt_ledger.backups import MAGIC
        assert result.content.startswith(MAGIC)


def test_export_tax_preference_is_explicit_and_survives_restart(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    with TestClient(app) as client:
        assert client.get("/api/config").json()["export_tax_mode"] == "reviewed"
        client.post("/api/settings", headers=HEADERS, json={"export_tax_mode": "exempt"}).raise_for_status()
        assert client.get("/api/config").json()["export_tax_mode"] == "exempt"
        assert client.post("/api/settings", headers=HEADERS, json={"export_tax_mode": "guess"}).status_code == 422
    with TestClient(create_app(tmp_path, run_workers=False)) as client:
        assert client.get("/api/config").json()["export_tax_mode"] == "exempt"
