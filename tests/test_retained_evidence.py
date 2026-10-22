import httpx
import pytest
from support import LedgerClient as TestClient

from receipt_ledger import pipeline
from receipt_ledger.schemas import Draft
from support import ready_app as create_app
from support import ready_store as Store


@pytest.mark.parametrize("key", ["native_pdf_text", "initial_text", "page_initial"])
def test_refund_in_retained_evidence_cannot_be_posted_individually_or_in_bulk(tmp_path, key):
    app = create_app(tmp_path, run_workers=False)
    store = app.state.store
    with TestClient(app) as client:
        row, _ = store.add("架空返金.pdf", ".pdf", b"refund-probe")
        ocr = {"text": "領収書 用紙 合計1650円", "pages": []}
        if key == "page_initial":
            ocr["pages"] = [{"initial_text": "返金明細 返金合計1650円"}]
        else:
            ocr[key] = "返金明細 返金合計1650円"
        store.complete(row["id"], ocr, None)
        row = store.get(row["id"])
        draft = Draft(date="2023-11-02", merchant="架空文具店", total=1650, account="消耗品費", payment="owner", purpose="業務用紙", business_percent=100)
        headers = {"X-Ledger-Request": "1"}
        result = client.post(f'/api/receipts/{row["id"]}/save', headers=headers, json={"version": row["version"], "draft": draft.model_dump(), "confirm": True})
        assert result.status_code == 422 and "返金" in result.text
        saved = store.save(row["id"], row["version"], draft)
        batch = client.post("/api/ledger/review", headers=headers, json={"rows": [{"id": row["id"], "version": saved["version"]}]}).json()
        assert not batch["reviewed"] and "返金" in batch["issues"][0]["message"]
        assert store.get(row["id"])["posting"] is None


def test_ai_failure_still_checks_refund_in_pdf_text_layer(tmp_path, monkeypatch):
    store = Store(tmp_path)
    row, _ = store.add("架空返金.pdf", ".pdf", b"fallback-refund")
    monkeypatch.setattr(pipeline, "read_receipt", lambda *args: {"text": "領収書 合計1650円", "native_pdf_text": "返金合計1650円", "pages": []})
    def unavailable(*args):
        raise httpx.ConnectError("local test model unavailable")
    monkeypatch.setattr(pipeline, "classify", unavailable)
    pipeline.process(store, row["id"])
    result = store.get(row["id"])
    assert result["suggestion"]["blocking_reasons"]
    assert "返金" in " ".join(result["suggestion"]["blocking_reasons"])
