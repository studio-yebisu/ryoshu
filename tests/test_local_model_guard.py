import json

import httpx
import pytest

from receipt_ledger import pipeline
from receipt_ledger.runtime import GenerateRequest, LlamaRuntime
from runtime_fakes import make_pin


@pytest.mark.parametrize("problem", ["placeholder-pin", "missing-pin", "corrupt-member", "unlisted-binary", "missing-model"])
def test_unverified_runtime_or_model_is_refused_before_any_process_or_receipt_request(tmp_path, monkeypatch, problem):
    pin_path, root = make_pin(tmp_path, extra=(lambda p: p["model"].update(sha256="<OUTSTANDING>")) if problem == "placeholder-pin" else None)
    if problem == "missing-pin":
        pin_path.unlink()
    elif problem == "corrupt-member":
        (root / "llama" / "synthetic-cpu" / "ggml-base.dll").write_bytes(b"synthetic dlL")
    elif problem == "unlisted-binary":
        (root / "llama" / "synthetic-cpu" / "extra.exe").write_bytes(b"MZ")
    elif problem == "missing-model":
        next((root / "models").glob("*.gguf")).unlink()
    runtime = LlamaRuntime(pin_path, root)
    monkeypatch.setattr(runtime, "_spawn", lambda *a: pytest.fail("no process may start"))
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("no request may be sent"))
    with pytest.raises(pipeline.ModelSafetyError) as caught:
        runtime.generate(GenerateRequest("system", "receipt text", {"type": "object"}))
    expected = {"placeholder-pin": "pin_invalid", "missing-pin": "pin_missing", "corrupt-member": "runtime_integrity",
                "unlisted-binary": "runtime_integrity", "missing-model": "model_missing"}[problem]
    assert caught.value.code == expected and "receipt text" not in str(caught.value)
    assert not runtime.status()["model_ready"] or problem == "corrupt-member"
    runtime.close()


def test_pipeline_no_longer_contacts_any_ollama_endpoint():
    assert not hasattr(pipeline, "local_models")
    source = (pipeline.__file__)
    text = open(source, encoding="utf-8").read()
    assert "11434" not in text and "/api/tags" not in text and "/api/chat" not in text
