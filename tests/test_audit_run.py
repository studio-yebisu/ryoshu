import hashlib
import importlib.util
import json
from pathlib import Path
import sys


import pytest


def audit_module():
    script = Path(__file__).resolve().parents[1] / "scripts/audit_receipts_100.py"
    spec = importlib.util.spec_from_file_location("audit_runtime_check", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("health", [
    {"model_ready": False, "runtime_ready": True, "model_sha256": "a" * 64, "model_issue_code": "model_unverified"},
    {"model_ready": True, "runtime_ready": False, "model_sha256": "a" * 64},
    {"model_ready": True, "runtime_ready": True, "model_sha256": ""},
    {"model_ready": True, "ollama_ready": True},
])
def test_audit_refuses_to_import_pdfs_without_a_ready_pinned_runtime(health):
    module = audit_module()
    with pytest.raises(RuntimeError, match="no PDFs were imported"):
        module.require_runtime(health, False)
    module.require_runtime(health, True)


def test_audit_records_runtime_fingerprint_fields_only():
    module = audit_module()
    health = {"model": "m", "model_sha256": "a" * 64, "runtime_version": "b11700", "runtime_ready": True, "model_ready": True,
              "model_issue_code": "", "runtime_state": "stopped", "data_dir": "secret-path", "instance": "x"}
    module.require_runtime(health, False)
    assert module.runtime_fingerprint(health) == {k: health[k] for k in module.RUNTIME_FIELDS}
    assert "data_dir" not in module.runtime_fingerprint(health)


def test_resume_of_a_completed_frozen_run_does_not_start_a_server(tmp_path, monkeypatch, capsys):
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts/audit_receipts_100.py"
    spec = importlib.util.spec_from_file_location("audit_resume_check", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / "audit"
    (output / "app").mkdir(parents=True)
    manifest = root / "samples/receipts-100-v2/manifest.json"
    (output / "run.json").write_text(json.dumps({"status": "complete", "source_hashes": {},
        "fixture_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(), "selected": ["V001"]}), "utf-8")
    (output / "observations.jsonl").write_text(json.dumps({"case_id": "V001"}) + "\n", "utf-8")
    monkeypatch.setattr(sys, "argv", [str(script), "--fixtures", str(manifest.parent), "--output", str(output),
                                     "--port", "0", "--cases", "V001", "--resume"])
    def unwanted(*args, **kwargs):
        raise AssertionError("A completed run must not start another process")
    monkeypatch.setattr(module.subprocess, "Popen", unwanted)
    module.main()
    assert json.loads(capsys.readouterr().out)["already_complete"]
    assert not (output / "data").exists()
