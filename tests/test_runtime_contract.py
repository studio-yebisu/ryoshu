import json

import pytest

from receipt_ledger.runtime import (DisabledRuntime, GenerateRequest, RuntimeFailure, child_environment, launch_arguments,
                                    parse_response, request_body)
from receipt_ledger.runtime import REASON_CODES
from receipt_ledger.runtime_copy import MESSAGES, UNKNOWN, is_placeholder, message

# Flags documented in the pinned llama.cpp b11700 tools/server/README.md (line numbers of that snapshot).
DOCUMENTED_B11700 = {"-m": 100, "--host": 193, "--port": 194, "--device": 78, "-ngl": 85, "-t": 38, "-c": 50, "-np": 177,
                     "--offline": 111, "--no-webui": 210, "--no-slots": 225, "--jinja": 232, "--reasoning": 234,
                     "--log-disable": 106}
FORBIDDEN = {"-hf", "-hfr", "--hf-repo", "-hff", "--hf-file", "-mu", "--model-url", "-dr", "--docker-repo", "--rpc",
             "--api-key", "--api-key-file", "--tools", "--mcp-servers-config", "--mcp-servers-json", "-ag", "--agent",
             "--models-dir", "--models-preset", "-mm", "--mmproj", "-mmu", "--mmproj-url", "--webui-mcp-proxy",
             "--ui-mcp-proxy", "--metrics", "--props", "--slots", "--reuse-port", "--path", "--log-file", "--lora"}


def test_argv_is_fixed_loopback_cpu_offline_and_documented():
    argv = launch_arguments("C:/r/llama/x/llama-server.exe", "C:/r/models/abc.gguf", 50123, 6, 8192)
    assert argv == ["C:/r/llama/x/llama-server.exe", "-m", "C:/r/models/abc.gguf", "--host", "127.0.0.1", "--port", "50123",
                    "--device", "none", "-ngl", "0", "-t", "6", "-c", "8192", "-np", "1", "--offline", "--no-webui",
                    "--no-slots", "--jinja", "--reasoning", "off", "--log-disable"]
    flags = {a for a in argv[1:] if a.startswith("-")}
    assert flags <= set(DOCUMENTED_B11700)
    assert not flags & FORBIDDEN


def test_child_environment_is_an_allow_list_and_carries_the_key_only_in_env():
    hostile = {"SystemRoot": r"C:\Windows", "NUMBER_OF_PROCESSORS": "8", "PATH": r"C:\evil",
               "LLAMA_ARG_HOST": "0.0.0.0", "LLAMA_ARG_HF_REPO": "x/y", "LLAMA_ARG_MODEL_URL": "https://x", "LLAMA_API_KEY": "old",
               "LLAMA_ARG_RPC": "10.0.0.1:50052", "GGML_RPC": "x", "HF_TOKEN": "t", "HF_HOME": "h", "HUGGING_FACE_HUB_TOKEN": "t",
               "HTTPS_PROXY": "http://p", "http_proxy": "http://p", "ALL_PROXY": "x", "NO_PROXY": "*", "OLLAMA_HOST": "x",
               "RECEIPT_LEDGER_DATA": "d", "USERPROFILE": r"C:\Users\u", "APPDATA": "a", "LOCALAPPDATA": "l"}
    env = child_environment(hostile, "per-launch-secret", r"C:\r\llama\x", r"C:\r\runtime\tmp\i")
    assert env == {"SYSTEMROOT": r"C:\Windows", "NUMBER_OF_PROCESSORS": "8",
                   "PATH": r"C:\r\llama\x" + ";" + r"C:\Windows\System32",
                   "TEMP": r"C:\r\runtime\tmp\i", "TMP": r"C:\r\runtime\tmp\i", "LLAMA_API_KEY": "per-launch-secret"}


def test_request_body_uses_the_natively_enforced_schema_form_and_explicit_non_thinking_template():
    schema = {"type": "object", "additionalProperties": False, "properties": {"total_choice": {"type": ["integer", "null"], "enum": [None, 1650]}},
              "required": ["total_choice"]}
    body = request_body(GenerateRequest(system="S", user="U\n/no_think", schema=schema))
    assert body == {"messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "U\n/no_think"}],
                    "response_format": {"type": "json_schema", "json_schema": {"name": "receipt", "schema": schema, "strict": True}},
                    "chat_template_kwargs": {"enable_thinking": False},
                    "temperature": 0.0, "seed": 42, "max_tokens": 850, "stream": False}
    assert body["response_format"]["json_schema"]["schema"] is schema
    # The README-example form was accepted but not enforced by the real b11700 server; never regress to it.
    assert "schema" not in body["response_format"]
    assert "model" not in body and "format" not in body and "think" not in body


def test_response_parsing_keeps_reasoning_evidence_but_never_its_text():
    content, finish, evidence = parse_response({"choices": [{"message": {"content": "{\"a\":1}", "reasoning_content": "hidden thoughts"},
                                                             "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
    assert (content, finish) == ("{\"a\":1}", "stop")
    assert evidence == {"finish_reason": "stop", "content_chars": 7, "reasoning_content_chars": 15,
                        "content_has_think_tag": False, "prompt_tokens": 5, "completion_tokens": 2}
    assert "hidden thoughts" not in json.dumps(evidence)
    _, _, empty = parse_response({"choices": [{"message": {"content": "<think>\n\n</think>{}"}, "finish_reason": "length"}]})
    assert empty["reasoning_content_chars"] == -1 and empty["content_has_think_tag"] and empty["finish_reason"] == "length"


@pytest.mark.parametrize("data", [None, {}, {"choices": []}, {"choices": [{"message": {"content": None}}]}, {"choices": ["x"]}])
def test_malformed_responses_are_rejected(data):
    with pytest.raises(ValueError):
        parse_response(data)


def test_disabled_runtime_never_generates():
    runtime = DisabledRuntime()
    assert runtime.status()["model_ready"] is False and runtime.status()["reason_code"] == "disabled"
    with pytest.raises(RuntimeFailure) as caught:
        runtime.generate(GenerateRequest("s", "u", {}))
    assert caught.value.code == "disabled" and str(caught.value) == "disabled"


def test_every_reason_code_has_adopted_copy_and_unknown_codes_never_echo_input():
    assert set(MESSAGES) == set(REASON_CODES)
    texts = [message(code) for code in REASON_CODES] + [UNKNOWN]
    assert all(isinstance(t, str) and t and not is_placeholder(t) and "COPY_SLOT" not in t for t in texts)
    assert len(set(texts)) == len(texts)
    assert not any("Ollama" in t or "qwen3:4b" in t for t in texts)
    assert all(code not in message(code) for code in REASON_CODES)
    hostile = "sk-secret-token 架空文具店 receipt body"
    for unknown in (hostile, "", None, 42):
        assert message(unknown) == UNKNOWN and "secret" not in message(unknown)
