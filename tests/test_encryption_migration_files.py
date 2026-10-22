from contextlib import closing
import hashlib
import sqlite3

import pytest

from receipt_ledger.store import Store
from receipt_ledger.protection import LedgerKeys, FILE_MAGIC
from support import PASSWORD, legacy_fixture


def test_legacy_original_sidecars_old_backups_and_deleted_db_records_are_protected(tmp_path):
    legacy=legacy_fixture(tmp_path)
    sentinel=b'old deleted confidential data'*12000
    with closing(sqlite3.connect(legacy.path)) as db:
        db.execute('PRAGMA secure_delete=OFF')
        db.execute('CREATE TABLE old_deleted(value BLOB)')
        db.execute('INSERT INTO old_deleted VALUES(?)',(sentinel,));db.commit()
        db.execute('DELETE FROM old_deleted');db.commit()
        assert b'old deleted confidential data' in legacy.path.read_bytes()
        backup_dir=tmp_path/'backups';backup_dir.mkdir()
        with closing(sqlite3.connect(backup_dir/'before-catalog-v2-old.sqlite3')) as target:db.backup(target)
    folder=next((tmp_path/'receipts').iterdir())
    (folder/'ocr.json').write_bytes(b'fictional confidential OCR sidecar')
    (folder/'normalized.png').write_bytes(b'fictional private normalized image')
    keys=LedgerKeys(tmp_path);keys.setup(PASSWORD)
    store=Store(tmp_path)
    assert store.ready
    for path in (tmp_path/'receipts').rglob('*'):
        if path.is_file():assert path.read_bytes().startswith(FILE_MAGIC)
    assert keys.read_file(folder/'ocr.json')==b'fictional confidential OCR sidecar'
    assert b'old deleted confidential data' not in (tmp_path/'ledger.sqlite3').read_bytes()
    assert (tmp_path/'ledger.sqlite3').stat().st_size <= 16384
    backup=backup_dir/'before-catalog-v2-old.sqlite3'
    assert not backup.read_bytes().startswith(b'SQLite format 3')
    with closing(store.database.open(backup,readonly=True)) as db:
        assert db.execute('SELECT count(*) FROM receipts').fetchone()[0]==2
    for row in store.list():
        assert hashlib.sha256(keys.read_file(store.folder(row['id'])/('original'+row['extension']))).hexdigest()==row['sha256']


def test_active_ledger_never_accepts_replaced_plaintext_original(tmp_path):
    legacy_fixture(tmp_path)
    keys=LedgerKeys(tmp_path);keys.setup(PASSWORD)
    store=Store(tmp_path);row=store.list()[0]
    original=store.folder(row['id'])/('original'+row['extension'])
    old=keys.read_file(original)
    original.write_bytes(old)
    reopened=Store(tmp_path)
    with pytest.raises(ValueError,match='暗号化された原本'):reopened.keys.read_file(original)
