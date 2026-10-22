"""Real Windows key protection and authenticated file/recovery negative tests."""
import json
import os
from pathlib import Path
import shutil

import pytest

from receipt_ledger import protection
from receipt_ledger.protection import LedgerKeys, ProtectionError, dpapi, unwrap_secret, wrap_secret

pytestmark = pytest.mark.skipif(os.name != "nt", reason="This app's protected key store uses Windows DPAPI")
PASSWORD = "fictional test recovery password"


def test_real_dpapi_restart_and_no_unprotected_key_material(tmp_path):
    keys = LedgerKeys(tmp_path)
    keys.setup(PASSWORD)
    assert dpapi(dpapi(b"fictional independent DPAPI probe"), decrypt=True) == b"fictional independent DPAPI probe"
    opened = LedgerKeys(tmp_path)
    assert opened.mode == "unlocked" and opened.derive("db") == keys.derive("db")
    assert keys.derive("db") != keys.derive("files")
    for path in [keys.key_path, keys.recovery_path]:
        assert keys.master not in path.read_bytes()
        assert PASSWORD.encode() not in path.read_bytes()


def test_copy_without_windows_key_requires_password_and_validates_before_rewrapping(tmp_path):
    source, destination = tmp_path / "a", tmp_path / "b"
    first = LedgerKeys(source)
    first.setup(PASSWORD)
    destination.mkdir()
    shutil.copyfile(first.recovery_path, destination / first.recovery_path.name)
    restored = LedgerKeys(destination)
    assert restored.mode == "locked" and not restored.key_path.exists()
    with pytest.raises(ProtectionError):
        restored.recover("wrong fictional password", lambda *args: pytest.fail("Wrong password reached validation"))
    assert not restored.key_path.exists()
    def validate(master, identity):
        assert master == first.master and identity == first.ledger_id
    restored.recover(PASSWORD, validate)
    assert LedgerKeys(destination).derive("db") == first.derive("db")


def test_failed_recovered_database_validation_does_not_replace_key_file(tmp_path):
    keys = LedgerKeys(tmp_path)
    keys.setup(PASSWORD)
    saved = keys.key_path.read_bytes()
    def invalid_database(*args):
        raise ProtectionError("Wrong ledger fixture")
    with pytest.raises(ProtectionError, match="Wrong ledger"):
        keys.recover(PASSWORD, invalid_database)
    assert keys.key_path.read_bytes() == saved


def test_missing_or_corrupt_key_never_creates_a_replacement_for_an_existing_vault(tmp_path):
    keys = LedgerKeys(tmp_path)
    keys.setup(PASSWORD)
    keys.key_path.write_bytes(b"corrupt fixture")
    locked = LedgerKeys(tmp_path)
    assert locked.mode == "locked"
    with pytest.raises(ProtectionError):
        locked.setup(PASSWORD)
    assert keys.key_path.read_bytes() == b"corrupt fixture"
    keys.key_path.unlink()
    keys.recovery_path.unlink()
    (tmp_path / ".ryoshu-vault.json").write_text('{"phase":"active"}', "utf-8")
    with pytest.raises(ProtectionError):
        LedgerKeys(tmp_path).setup(PASSWORD)
    assert not keys.key_path.exists()


def test_file_encryption_hides_content_and_detects_modification_and_path_substitution(tmp_path):
    keys = LedgerKeys(tmp_path)
    keys.setup(PASSWORD)
    original = tmp_path / "receipts/fictional/original.pdf"
    body = b"%PDF fictional private receipt 12345"
    keys.write_file(original, body)
    assert body not in original.read_bytes() and keys.read_file(original) == body
    other = original.with_name("preview.jpg")
    shutil.copyfile(original, other)
    with pytest.raises(ProtectionError):
        keys.read_file(other)
    raw = original.read_bytes()
    original.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    with pytest.raises(ProtectionError):
        keys.read_file(original)


def test_plaintext_files_and_paths_outside_the_ledger_are_rejected(tmp_path):
    keys = LedgerKeys(tmp_path / "data")
    keys.setup(PASSWORD)
    plain = keys.root / "original.pdf"
    plain.write_bytes(b"%PDF plain fixture")
    with pytest.raises(ProtectionError):
        keys.read_file(plain)
    with pytest.raises(ProtectionError):
        keys.write_file(tmp_path / "outside.pdf", b"must not be written")
    assert not (tmp_path / "outside.pdf").exists()


@pytest.mark.parametrize("change", [{"version": True}, {"n": 2**40}, {"salt": "invalid!"}, {"nonce": ""}, {"ciphertext": ""}])
def test_untrusted_recovery_headers_do_not_control_kdf_cost_or_bypass_authentication(change):
    envelope = wrap_secret(b"fictional master", PASSWORD, b"test scope")
    envelope.update(change)
    with pytest.raises(ProtectionError):
        unwrap_secret(envelope, PASSWORD, b"test scope")


def test_recovery_password_and_context_are_both_required():
    envelope = wrap_secret(b"fictional master", PASSWORD, b"ledger a")
    with pytest.raises(ProtectionError):
        unwrap_secret(envelope, PASSWORD, b"ledger b")


def test_aborted_atomic_write_keeps_previous_protected_file(tmp_path, monkeypatch):
    target = tmp_path / "protected.fixture"
    target.write_bytes(b"previous encrypted fixture")
    def interrupted(*args):
        raise OSError("simulated interrupted switch")
    monkeypatch.setattr(protection.os, "replace", interrupted)
    with pytest.raises(OSError):
        protection.atomic_write(target, b"new encrypted fixture")
    assert target.read_bytes() == b"previous encrypted fixture"
    assert list(tmp_path.iterdir()) == [target]
