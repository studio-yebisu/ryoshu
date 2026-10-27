import json
import os

import pytest

from receipt_ledger import config
from receipt_ledger.runtime_pin import PinError, load_pin, main, ready_state, runtime_paths
from runtime_fakes import make_pin


def test_synthetic_pin_loads_and_derives_paths_only_from_root_and_pin(tmp_path):
    pin_path, root = make_pin(tmp_path)
    pin = load_pin(pin_path)
    paths = runtime_paths(pin, root)
    assert paths.runtime_dir == root / "llama" / "synthetic-cpu"
    assert paths.model == root / "models" / (pin.model_sha256 + ".gguf")
    assert paths.marker.name == pin.model_sha256 + ".gguf.verified.json"
    assert paths.owners_dir == root / "runtime" / "owners"
    assert pin.model_id == "synthetic/test-model/synthetic.gguf"


@pytest.mark.parametrize("mutate", [
    lambda p: p["model"].update(sha256="<OUTSTANDING>"),
    lambda p: p["model"].update(bytes=0),
    lambda p: p["model"].update(revision="<OUTSTANDING>"),
    lambda p: p["runtime"]["members"].append({"name": "../escape.dll", "bytes": 1, "sha256": "f" * 64}),
    lambda p: p["runtime"]["members"].append({"name": "LLAMA-SERVER.EXE", "bytes": 1, "sha256": "f" * 64}),
    lambda p: p["runtime"].update(server_exe="other.exe"),
    lambda p: p["runtime"].update(dir_name=".."),
    lambda p: p.update(schema=2),
    lambda p: p["runtime"].update(members=[]),
])
def test_placeholder_or_unsafe_pin_values_are_refused(tmp_path, mutate):
    pin_path, root = make_pin(tmp_path, extra=mutate)
    with pytest.raises(PinError) as caught:
        load_pin(pin_path)
    assert caught.value.code == "pin_invalid"
    assert ready_state(pin_path, root)["reason_code"] == "pin_invalid"


def test_missing_pin_is_reported_without_touching_runtime_files(tmp_path):
    state = ready_state(tmp_path / "absent.json", tmp_path)
    assert state == {**state, "runtime_ok": False, "model_ok": False, "reason_code": "pin_missing"}


def test_product_pin_absent_or_unprovisioned_root_is_not_ready(tmp_path):
    # The real pin is authored by the installer; until then (or with an empty root) nothing is ready.
    state = ready_state(config.RUNTIME_PIN, tmp_path)
    assert not (state["runtime_ok"] and state["model_ok"])
    assert state["reason_code"] in {"pin_missing", "pin_invalid", "runtime_missing"}


def test_ready_state_reports_each_provisioning_problem(tmp_path):
    pin_path, root = make_pin(tmp_path)
    assert ready_state(pin_path, root)["runtime_ok"] and ready_state(pin_path, root)["model_ok"]
    pin = load_pin(pin_path)
    paths = runtime_paths(pin, root)

    paths.marker.unlink()
    assert ready_state(pin_path, root)["reason_code"] == "model_unverified"
    info = paths.model.stat()
    paths.marker.write_text(json.dumps({"size": info.st_size, "mtime_ns": info.st_mtime_ns - 1, "sha256": pin.model_sha256}), "utf-8")
    assert ready_state(pin_path, root)["reason_code"] == "model_unverified"
    paths.model.unlink()
    assert ready_state(pin_path, root)["reason_code"] == "model_missing"

    (paths.runtime_dir / "injected.dll").write_bytes(b"x")
    assert ready_state(pin_path, root)["reason_code"] == "runtime_integrity"
    (paths.runtime_dir / "injected.dll").unlink()
    paths.server.write_bytes(b"MZ synthetic serveR")
    assert ready_state(pin_path, root)["reason_code"] == "runtime_integrity"
    paths.server.write_bytes(b"short")
    assert ready_state(pin_path, root)["reason_code"] == "runtime_integrity"
    paths.server.unlink()
    assert ready_state(pin_path, root)["reason_code"] == "runtime_missing"


def test_readiness_marker_cannot_override_the_source_pin(tmp_path):
    pin_path, root = make_pin(tmp_path)
    paths = runtime_paths(load_pin(pin_path), root)
    info = paths.model.stat()
    paths.marker.write_text(json.dumps({"size": info.st_size, "mtime_ns": info.st_mtime_ns, "sha256": "0" * 64}), "utf-8")
    assert ready_state(pin_path, root)["reason_code"] == "model_unverified"


def test_ready_state_never_hashes_the_model(tmp_path, monkeypatch):
    from receipt_ledger import runtime_pin
    pin_path, root = make_pin(tmp_path)
    model = runtime_paths(load_pin(pin_path), root).model
    real = runtime_pin.file_sha256
    hashed = []
    monkeypatch.setattr(runtime_pin, "file_sha256", lambda path: hashed.append(path) or real(path))
    cache = {}
    ready_state(pin_path, root, hash_cache=cache)
    ready_state(pin_path, root, hash_cache=cache)
    assert model not in hashed and len(hashed) == 1


def test_check_only_cli_prints_state_and_exit_code(tmp_path, capsys):
    pin_path, root = make_pin(tmp_path)
    assert main(["--root", str(root), "--pin", str(pin_path)]) == 0
    assert json.loads(capsys.readouterr().out)["model_ok"] is True
    assert main(["--root", str(tmp_path / "empty"), "--pin", str(pin_path)]) == 2
