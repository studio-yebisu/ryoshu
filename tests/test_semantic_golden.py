"""The migrated transport must send exactly the d3186ec9 prompt text and schema and keep post-processing."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace

import httpx
import pytest

from receipt_ledger import config, pipeline, semantic
from receipt_ledger.runtime_copy import message as runtime_message
from runtime_fakes import ScriptedRuntime

GOLDEN = Path(__file__).with_name("golden_semantic_d3186ec9.py")
GOLDEN_SHA256 = "f4d0e0f4e275b140d4876ae3e7bb3abc9d9b78df0af0102fc6967bbad1b65f66"
RUNTIME_ONLY = {"model", "model_digest", "seconds", "runtime", "runtime_pin_sha256", "model_load_seconds", "runtime_evidence"}

LONG = "\n".join(f"明細{i} コピー用紙 A4 500枚 550円" for i in range(400))
CASES = {
    "single_total": {"text": "架空文具店 株式会社\n2024年5月10日\n領収書\nコピー用紙 A4\n合計 1,650円\n(内消費税10% 150円)\n登録番号 T1234567890123", "pages": []},
    "native_layer": {"text": "領収書 合計1650円", "native_pdf_text": "株式会社架空書房\n2024年4月1日\n書籍代\n合計 1,650円", "pages": [{"initial_text": "株式会社架空書房"}]},
    "uncertain_total": {"text": "架空交通 株式会社\n2024/03/02\n乗車券 1,200円\n特急券 900円\nお預り 5,000円", "pages": []},
    "no_issuer": {"text": "2024年1月5日\nコピー用紙\n1,100円", "pages": []},
    "long_text": {"text": "株式会社架空資材センター\n2024年6月1日\n" + LONG + "\n合計 220,000円", "pages": []},
}
CONTENT = json.dumps({"merchant": "", "description": "コピー用紙", "account": "消耗品費", "reason": "用紙の購入",
                      "hold_reason": "", "confidence": "high"}, ensure_ascii=False)


def golden_module():
    assert hashlib.sha256(GOLDEN.read_bytes()).hexdigest() == GOLDEN_SHA256
    spec = importlib.util.spec_from_file_location("receipt_ledger._golden_semantic", GOLDEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_golden(monkeypatch, ocr, content):
    module = golden_module()
    sent = []
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": content}, "done_reason": "stop"})
    real = httpx.Client
    monkeypatch.setattr(module, "httpx", SimpleNamespace(Client=lambda **kw: real(transport=httpx.MockTransport(handler), **kw), Timeout=httpx.Timeout))
    monkeypatch.setattr(pipeline, "local_models", lambda: [{"name": "qwen3:4b", "digest": "old"}], raising=False)
    monkeypatch.setattr(config, "MODEL", "qwen3:4b", raising=False)
    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:11434", raising=False)
    result = module.classify(json.loads(json.dumps(ocr)), "ソフトウェア制作")
    assert len(sent) == 1
    return sent[0], result


@pytest.mark.parametrize("case", sorted(CASES))
def test_system_user_and_schema_are_byte_identical_to_original_source(case, monkeypatch):
    old_body, old_result = run_golden(monkeypatch, CASES[case], CONTENT)
    runtime = ScriptedRuntime([CONTENT])
    new_result = semantic.classify(json.loads(json.dumps(CASES[case])), "ソフトウェア制作", None, None, None, runtime)
    request = runtime.requests[0]
    assert old_body["messages"] == [{"role": "system", "content": request.system}, {"role": "user", "content": request.user}]
    assert request.user.endswith("\n/no_think")
    assert old_body["format"] == request.schema
    assert (request.max_tokens, request.temperature, request.seed, request.timeout_s) == (850, 0.0, 42, 300.0)
    assert old_body["options"]["num_predict"] == 850 and old_body["options"]["temperature"] == 0 and old_body["options"]["seed"] == 42
    strip = lambda r: {k: v for k, v in r.items() if k not in RUNTIME_ONLY}
    assert strip(new_result) == strip(old_result)
    assert new_result["model"] == "scripted/model" and new_result["model_digest"] == "d" * 64
    assert new_result["runtime_evidence"] == {"finish_reason": "stop"}


def test_length_limit_and_invalid_shape_keep_original_errors(monkeypatch):
    ocr = CASES["single_total"]
    with pytest.raises(ValueError, match="出力上限"):
        semantic.classify(dict(ocr), "", None, None, None, ScriptedRuntime([CONTENT], finish_reason="length"))
    with pytest.raises(ValueError, match="^" + re.escape(runtime_message("invalid_structured_output")) + "$"):
        semantic.classify(dict(ocr), "", None, None, None, ScriptedRuntime([json.dumps({"description": 1})]))


def test_missing_runtime_is_a_runtime_failure_not_a_request():
    with pytest.raises(pipeline.ModelSafetyError) as caught:
        semantic.classify(dict(CASES["single_total"]), "")
    assert caught.value.code == "disabled"
