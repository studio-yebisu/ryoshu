from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from receipt_ledger.backups import create_backup
from receipt_ledger.database import CipherDatabase, fingerprint
from receipt_ledger.importing import Importer
from receipt_ledger.protection import LedgerKeys, ProtectionError, FILE_MAGIC, private_work, clean_work
from receipt_ledger.restore import restore_backup
from receipt_ledger.server import create_app
from receipt_ledger.store import Store
from support import PASSWORD, legacy_fixture, ready_store
from test_session_security import HEADERS, sign_in


def test_missing_original_is_detected_before_legacy_cutover_and_has_a_retry_screen_api(tmp_path):
    legacy=legacy_fixture(tmp_path)
    original=next((tmp_path/'receipts').glob('*/original.pdf'))
    raw=original.read_bytes();original.unlink()
    keys=LedgerKeys(tmp_path);keys.setup(PASSWORD)
    store=Store(tmp_path)
    assert not store.ready and '移行前の原本' in store.initialization_error
    with closing(sqlite3.connect(legacy.path)) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==2
        assert db.execute('SELECT count(*) FROM receipts').fetchone()[0]==2
    assert not (tmp_path/'ledger.enc.sqlite3').exists()
    app=create_app(tmp_path,run_workers=False)
    with TestClient(app) as client:
        sign_in(client,app)
        status=client.get('/api/security').json()
        assert status['can_retry'] and status['error'] and not status['ready']
        assert client.get('/api/receipts').status_code==423
        original.write_bytes(raw)
        assert client.post('/api/security/retry',headers=HEADERS,json={}).status_code==200
        assert len(client.get('/api/receipts').json()['receipts'])==2


def test_abandoned_atomic_ciphertext_is_removed_without_aad_failure_on_migration(tmp_path):
    legacy_fixture(tmp_path)
    keys=LedgerKeys(tmp_path);keys.setup(PASSWORD)
    folder=next((tmp_path/'receipts').iterdir())
    intended=folder/'preview-0.jpg'
    keys.write_file(intended,b'fictional private preview')
    pending=folder/'.ryoshu-write-abandoned'
    intended.rename(pending)
    store=Store(tmp_path)
    assert store.ready and not pending.exists()


def test_hot_rollback_journal_after_actual_process_exit_recovers_before_migration(tmp_path):
    legacy=legacy_fixture(tmp_path)
    with closing(sqlite3.connect(legacy.path)) as db:
        db.execute('CREATE TABLE large_old(value BLOB)')
        db.executemany('INSERT INTO large_old VALUES(?)',[(b'fictional old deleted bytes'*10000,)]*12)
        db.commit();before=fingerprint(db)
    keys=LedgerKeys(tmp_path);keys.setup(PASSWORD)
    program='''
import os,sys
from pathlib import Path
from receipt_ledger.database import CipherDatabase
from receipt_ledger.protection import LedgerKeys
def crash(self,db):
    db.execute('PRAGMA foreign_keys=OFF')
    db.execute('PRAGMA cache_size=2')
    db.execute('BEGIN EXCLUSIVE')
    db.execute('DROP TABLE large_old')
    os._exit(91)
CipherDatabase._make_guard=crash
root=Path(sys.argv[1])
CipherDatabase(root,LedgerKeys(root))
'''
    result=subprocess.run([sys.executable,'-c',program,str(tmp_path)],capture_output=True,timeout=30)
    assert result.returncode==91,result.stderr.decode('utf8',errors='replace')
    assert (tmp_path/'ledger.sqlite3-journal').exists()
    resumed=CipherDatabase(tmp_path,keys)
    with closing(resumed.open(readonly=True)) as db:assert fingerprint(db)==before
    assert not (tmp_path/'ledger.sqlite3-journal').exists()


def test_unlocked_windows_account_can_reset_forgotten_recovery_password(tmp_path):
    store=ready_store(tmp_path)
    row,_=store.add('fictional.png','.png',b'fictional password reset original')
    before=store.keys.master
    app=create_app(tmp_path,run_workers=False)
    replacement='new fictional recovery password'
    with TestClient(app) as client:
        sign_in(client,app)
        response=client.post('/api/security/password',headers=HEADERS,json={'password':replacement,'confirmation':replacement})
        assert response.status_code==200
        assert replacement not in response.text
        assert app.state.store.keys.master==before
        assert app.state.store.get(row['id'])['sha256']==row['sha256']
        with pytest.raises(ProtectionError):app.state.store.keys.verify_password(PASSWORD)
        app.state.store.keys.verify_password(replacement)
        assert client.post('/api/backup',headers=HEADERS,json={'password':replacement}).status_code==429


def test_rescue_backup_explicitly_omits_missing_file_restores_ledger_and_can_repair_by_exact_reimport(tmp_path):
    store=ready_store(tmp_path/'data');importer=Importer(store)
    raw=b'fictional recoverable original'
    row,_=store.add('fictional.pdf','.pdf',raw)
    (store.folder(row['id'])/'original.pdf').unlink()
    with pytest.raises(ProtectionError):create_backup(store,importer,PASSWORD)
    temporary,archive=create_backup(store,importer,PASSWORD,allow_missing=True)
    try:
        result=restore_backup(archive,tmp_path/'restored',PASSWORD)
    finally:temporary.cleanup()
    assert result['receipts']==1 and result['missing_files']==1
    recovered=ready_store(result['restored_data'])
    assert recovered.storage_issues()['missing_originals']==1
    repaired,duplicate=recovered.add('reimport.pdf','.pdf',raw)
    assert duplicate and repaired['id']==row['id']
    assert recovered.storage_issues()['missing_originals']==0
    assert recovered.keys.read_file(recovered.folder(row['id'])/'original.pdf')==raw
    assert recovered.history(row['id'])[0]['action']=='repair_original'


def test_private_work_cleanup_skips_a_live_job_and_cleans_after_it_exits(tmp_path):
    with private_work(tmp_path) as folder:
        (folder/'private.png').write_bytes(b'fictional OCR work')
        clean_work(tmp_path)
        assert folder.exists()
    assert not folder.exists()
    abandoned=tmp_path/'.ryoshu-work/ocr-abandoned'
    abandoned.mkdir();(abandoned/'private.png').write_bytes(b'fictional abandoned work')
    clean_work(tmp_path)
    assert not abandoned.exists()
