import hashlib
import importlib.util
import json
from pathlib import Path
import sys


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
