import io
import json
import zipfile
import subprocess
import sys
import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from receipt_ledger.restore import restore_backup
from receipt_ledger.server import create_app
from receipt_ledger.store import Store


def backup(tmp_path):
    app = create_app(tmp_path / "original", run_workers=False)
    with TestClient(app) as client:
        row, _ = app.state.store.add("架空.png", ".png", b"original receipt bytes")
        archive = tmp_path / "backup.zip"
        archive.write_bytes(client.get("/api/backup").content)
    return archive, row


def test_restore_uses_new_directory_verifies_hashes_and_keeps_old_ledger(tmp_path):
    archive, row = backup(tmp_path)
    destination = tmp_path / "restored"
    result = restore_backup(archive, destination)
    assert result["receipts"] == 1
    assert Store(destination / "data").get(row["id"])["sha256"] == row["sha256"]
    assert Store(tmp_path / "original").get(row["id"])["sha256"] == row["sha256"]
    with pytest.raises(ValueError, match="存在"):
        restore_backup(archive, destination)


def test_restore_cli_preserves_japanese_paths_on_an_ascii_console(tmp_path):
    archive, _ = backup(tmp_path)
    destination = tmp_path / "日本語の復元先"
    command = Path(__file__).resolve().parents[1] / "scripts/restore_backup.py"
    result = subprocess.run([sys.executable, str(command), str(archive), str(destination)],
                            capture_output=True, env={**os.environ, "PYTHONIOENCODING": "ascii"}, timeout=20)
    assert result.returncode == 0, result.stderr.decode("ascii", errors="replace")
    assert json.loads(result.stdout.decode("ascii"))["restored_data"] == str(destination / "data")


def test_path_traversal_and_unknown_files_are_rejected_before_destination_creation(tmp_path):
    for name in ("../escape.txt", "data/../../escape.txt", "data/imports/fake/source.pdf", "script.ps1"):
        archive = tmp_path / "bad.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("manifest.json", json.dumps({"app": "Ryoshu", "receipts": 0}))
            bundle.writestr("data/ledger.sqlite3", b"not a database")
            bundle.writestr(name, b"wrong")
        with pytest.raises(ValueError):
            restore_backup(archive, tmp_path / "target")
        assert not (tmp_path / "target").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_tampered_receipt_fails_validation_and_is_not_adopted(tmp_path):
    archive, row = backup(tmp_path)
    bad = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(bad, "w") as output:
        for name in source.namelist():
            output.writestr(name, b"altered" if "/original" in name else source.read(name))
    with pytest.raises(ValueError, match="原本"):
        restore_backup(bad, tmp_path / "failed")
    assert (tmp_path / "failed/RESTORE_FAILED.txt").is_file()
    assert Store(tmp_path / "original").get(row["id"])["sha256"] == row["sha256"]
