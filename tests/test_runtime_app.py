import json

import pytest

from receipt_ledger import pipeline
from receipt_ledger.runtime import LlamaRuntime
from receipt_ledger.runtime_copy import is_placeholder, message
from receipt_ledger.schemas import Draft
from runtime_fakes import ScriptedRuntime, make_pin
from support import LedgerClient, ready_app, ready_store

OCR = {"text": "架空文具店 株式会社\n2024年5月10日\n領収書\nコピー用紙 A4\n合計 1,650円", "pages": []}
CONTENT = json.dumps({"merchant": "架空文具店 株式会社", "description": "コピー用紙", "account": "消耗品費", "reason": "用紙",
                      "hold_reason": "", "confidence": "high"}, ensure_ascii=False)


def test_public_health_shape_is_unchanged_and_default_app_is_inference_disabled(tmp_path):
    app = ready_app(tmp_path)
    with LedgerClient(app) as client:
        client.headers.pop("Authorization")
        assert set(client.get("/api/health").json()) == {"app", "app_version", "instance"}
    with LedgerClient(ready_app(tmp_path / "b")) as client:
        health = client.get("/api/health").json()
    assert "ollama_ready" not in health
    assert health["model_ready"] is False and health["runtime_ready"] is False
    assert health["model_issue_code"] == "disabled" and health["runtime_state"] == "disabled"
    assert health["model_issue"] == message("disabled") and not is_placeholder(health["model_issue"])


def test_health_reports_pinned_identity_without_spawning_or_exposing_runtime_secrets(tmp_path, monkeypatch):
    pin_path, root = make_pin(tmp_path / "rt")
    runtime = LlamaRuntime(pin_path, root)
    monkeypatch.setattr(runtime, "_spawn", lambda *a: pytest.fail("health must not start the runtime"))
    app = ready_app(tmp_path / "data", run_workers=False, runtime=runtime)
    with LedgerClient(app) as client:
        health = client.get("/api/health").json()
        assert health["model_ready"] and health["runtime_ready"] and health["model_issue"] == "" and health["model_issue_code"] == ""
        assert health["runtime_version"] == "synthetic-test" and health["model"] == "synthetic/test-model/synthetic.gguf"
        assert health["runtime_state"] == "stopped"
        text = json.dumps(health)
        assert "LLAMA_API_KEY" not in text and "port" not in health and "pid" not in health
    assert runtime.status()["state"] == "closed"


def test_lifespan_closes_injected_runtime_once(tmp_path):
    runtime = ScriptedRuntime()
    with LedgerClient(ready_app(tmp_path, run_workers=False, runtime=runtime)):
        pass
    assert runtime.closed == 1


def test_run_workers_false_never_reaches_the_runtime(tmp_path):
    runtime = ScriptedRuntime([CONTENT])
    app = ready_app(tmp_path, run_workers=False, runtime=runtime)
    store = app.state.store
    with LedgerClient(app) as client:
        row, _ = store.add("架空.pdf", ".pdf", b"probe")
        store.complete(row["id"], OCR, None, "x")
        current = store.get(row["id"])
        assert client.post(f"/api/receipts/{row['id']}/retry", headers={"X-Ledger-Request": "1"}, json={"version": current["version"]}).status_code == 200
    assert runtime.requests == []


def test_worker_uses_injected_runtime_and_records_pinned_identity(tmp_path, monkeypatch):
    store = ready_store(tmp_path)
    row, _ = store.add("架空.pdf", ".pdf", b"worker-probe")
    monkeypatch.setattr(pipeline, "read_receipt", lambda *a: dict(OCR))
    runtime = ScriptedRuntime([CONTENT])
    pipeline.process(store, row["id"], runtime)
    result = store.get(row["id"])
    assert not result["error"] and len(runtime.requests) == 1
    assert result["suggestion"]["model"] == "scripted/model" and result["suggestion"]["model_digest"] == "d" * 64
    assert result["suggestion"]["runtime"] == "scripted" and result["suggestion"]["runtime_pin_sha256"] == "e" * 64


@pytest.mark.parametrize("code", ["pin_missing", "model_integrity", "start_failed", "exited", "load_timeout", "request_timeout",
                                  "cancelled", "auth_or_owner_mismatch", "crash_loop", "disabled"])
def test_every_runtime_failure_keeps_ocr_fallback_and_manual_entry(tmp_path, monkeypatch, code):
    app = ready_app(tmp_path, run_workers=False)
    store = app.state.store
    row, _ = store.add("架空.pdf", ".pdf", b"fallback-" + code.encode())
    monkeypatch.setattr(pipeline, "read_receipt", lambda *a: {"text": OCR["text"], "native_pdf_text": "返金合計1650円", "pages": []})
    pipeline.process(store, row["id"], ScriptedRuntime(error=code))
    result = store.get(row["id"])
    assert result["ocr"]["text"] == OCR["text"] and result["error"]
    assert "返金" in " ".join(result["suggestion"]["blocking_reasons"])
    assert code not in {"request_timeout"} or "時間内" in result["error"]
    draft = Draft(date="2024-05-10", merchant="架空文具店", total=1650, account="消耗品費", payment="owner", purpose="用紙", business_percent=100)
    with LedgerClient(app) as client:
        saved = client.post(f"/api/receipts/{row['id']}/save", headers={"X-Ledger-Request": "1"},
                            json={"version": result["version"], "draft": draft.model_dump(), "confirm": False})
        assert saved.status_code == 200
        assert client.get("/api/export/journal").status_code == 200
