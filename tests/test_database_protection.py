"""Real SQLCipher migration, crash boundaries and legacy-version guards."""
from contextlib import closing
import os
import sqlite3

import pytest

from receipt_ledger.database import CipherDatabase, GUARD_VERSION, fingerprint
from receipt_ledger.protection import LedgerKeys, ProtectionError
from receipt_ledger.store import Store
from support import legacy_fixture

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows user-scoped key protection")
PASSWORD = "fictional migration test password"


def legacy(root):
    store = legacy_fixture(root)
    with closing(sqlite3.connect(store.path)) as connection:
        before = fingerprint(connection)
    keys = LedgerKeys(root)
    keys.setup(PASSWORD)
    return store, keys, before


def test_real_legacy_ledger_migration_preserves_every_row_and_has_a_data_free_old_version_guard(tmp_path):
    store, keys, expected = legacy(tmp_path)
    database = CipherDatabase(tmp_path, keys)
    with closing(database.open(readonly=True)) as db:
        assert fingerprint(db) == expected
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    with closing(sqlite3.connect(database.legacy_path)) as guard:
        assert guard.execute("PRAGMA user_version").fetchone()[0] == GUARD_VERSION
        assert guard.execute("SELECT ledger_id FROM ryoshu_protected").fetchone()[0] == keys.ledger_id
        assert not guard.execute("SELECT 1 FROM sqlite_master WHERE name='receipts'").fetchone()
    for path in [database.path, database.legacy_path]:
        assert "架空の機密OCRセンチネル".encode() not in path.read_bytes()
        assert "架空の機密取引先".encode() not in path.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):
        with closing(sqlite3.connect(database.path)) as unprotected:
            unprotected.execute("SELECT * FROM receipts").fetchall()
    # The previous public app fails closed instead of creating a fresh plaintext ledger.
    # v1.0.0 rejects every schema version above 2 before creating its tables.
    with closing(sqlite3.connect(database.legacy_path)) as old_connection:
        assert old_connection.execute("PRAGMA user_version").fetchone()[0] > 2
    assert list((tmp_path / "backups").glob("before-encryption-*.sqlite3"))


def test_exclusive_legacy_lock_is_held_until_the_guard_commits(tmp_path, monkeypatch):
    _, keys, _ = legacy(tmp_path)
    actual = CipherDatabase._make_guard
    checked = []
    def guarded(self, source):
        with closing(sqlite3.connect(self.legacy_path, timeout=0)) as rival:
            with pytest.raises(sqlite3.DatabaseError):
                rival.execute("UPDATE receipts SET original_name='must be blocked'")
        checked.append(True)
        actual(self, source)
    monkeypatch.setattr(CipherDatabase, "_make_guard", guarded)
    CipherDatabase(tmp_path, keys)
    assert checked == [True]


def test_restart_after_export_uses_the_current_legacy_ledger_not_a_stale_snapshot(tmp_path, monkeypatch):
    _, keys, _ = legacy(tmp_path)
    actual = CipherDatabase._make_guard
    def interrupted(*args):
        raise OSError("simulated crash before cutover")
    monkeypatch.setattr(CipherDatabase, "_make_guard", interrupted)
    with pytest.raises(OSError):
        CipherDatabase(tmp_path, keys)
    with closing(sqlite3.connect(tmp_path / "ledger.sqlite3")) as old:
        old.execute("UPDATE receipts SET original_name='架空の中断後の変更.pdf'")
        old.commit()
        expected = fingerprint(old)
    monkeypatch.setattr(CipherDatabase, "_make_guard", actual)
    resumed = CipherDatabase(tmp_path, keys)
    with closing(resumed.open(readonly=True)) as current:
        assert fingerprint(current) == expected


def test_restart_after_guard_commit_finishes_verified_cutover(tmp_path, monkeypatch):
    _, keys, expected = legacy(tmp_path)
    actual = CipherDatabase._save_state
    def interrupted(self, phase, **details):
        if phase == "active":
            raise OSError("simulated crash after guard commit")
        return actual(self, phase, **details)
    monkeypatch.setattr(CipherDatabase, "_save_state", interrupted)
    with pytest.raises(OSError):
        CipherDatabase(tmp_path, keys)
    monkeypatch.setattr(CipherDatabase, "_save_state", actual)
    resumed = CipherDatabase(tmp_path, keys)
    with closing(resumed.open(readonly=True)) as current:
        assert fingerprint(current) == expected
    assert resumed._state()["phase"] == "active"


def test_lost_ciphertext_and_tampered_state_never_create_an_empty_replacement(tmp_path):
    _, keys, _ = legacy(tmp_path)
    database = CipherDatabase(tmp_path, keys)
    saved = database.path.read_bytes()
    database.path.unlink()
    with pytest.raises(ProtectionError):
        CipherDatabase(tmp_path, keys)
    assert not database.path.exists()
    database.path.write_bytes(saved)
    raw = database.state_path.read_bytes()
    database.state_path.write_bytes(raw.replace(b"active", b"broken"))
    with pytest.raises(ProtectionError):
        CipherDatabase(tmp_path, keys)
    assert database.path.read_bytes() == saved


def test_new_protected_database_and_independently_keyed_snapshot_are_encrypted(tmp_path):
    keys = LedgerKeys(tmp_path)
    keys.setup(PASSWORD)
    database = CipherDatabase(tmp_path, keys)
    with closing(database.open()) as db:
        db.execute("CREATE TABLE fictional(value)")
        db.execute("INSERT INTO fictional VALUES('secret fixture')")
        db.commit()
    import secrets
    backup_key = secrets.token_bytes(32)
    backup = tmp_path / "snapshot.sqlite3"
    database.snapshot(backup, key=backup_key)
    with closing(database.open(backup, key=backup_key, readonly=True)) as restored:
        assert restored.execute("SELECT value FROM fictional").fetchone()[0] == "secret fixture"
    with pytest.raises(ProtectionError):
        database.open(backup, readonly=True)
    assert b"secret fixture" not in backup.read_bytes()


def test_locked_key_cannot_create_a_database(tmp_path):
    keys = LedgerKeys(tmp_path)
    with pytest.raises(ProtectionError):
        CipherDatabase(tmp_path, keys)
    assert not (tmp_path / "ledger.enc.sqlite3").exists()
