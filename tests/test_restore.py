import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import zipfile

import pytest

from receipt_ledger.backups import MAGIC, write_encrypted
from receipt_ledger.restore import restore_backup
from receipt_ledger.protection import ProtectionError
from support import PASSWORD, backup_files, legacy_fixture, ready_app, ready_store, LedgerClient


def backup(tmp_path):
    app=ready_app(tmp_path/'original',run_workers=False)
    with LedgerClient(app) as client:
        row,_=app.state.store.add('架空.png','.png',b'original receipt bytes')
        archive=tmp_path/'backup.ryoshubak'
        result=client.post('/api/backup',headers={'X-Ledger-Request':'1'},json={'password':PASSWORD})
        result.raise_for_status();archive.write_bytes(result.content)
    return archive,row


def test_restore_new_directory_rekeys_verifies_hashes_and_keeps_original(tmp_path):
    archive,row=backup(tmp_path)
    assert archive.read_bytes().startswith(MAGIC)
    assert b'original receipt bytes' not in archive.read_bytes()
    destination=tmp_path/'restored'
    result=restore_backup(archive,destination,PASSWORD)
    assert result['receipts']==1
    old,new=ready_store(tmp_path/'original'),ready_store(destination/'data')
    assert new.get(row['id'])['sha256']==old.get(row['id'])['sha256']==row['sha256']
    assert new.keys.master!=old.keys.master and new.keys.ledger_id!=old.keys.ledger_id
    assert new.keys.read_file(new.folder(row['id'])/'original.png')==b'original receipt bytes'
    with pytest.raises(ValueError,match='存在'):restore_backup(archive,destination,PASSWORD)


def test_restore_cli_japanese_path_ascii_console_and_password_uses_stdin(tmp_path):
    archive,_=backup(tmp_path)
    destination=tmp_path/'日本語の復元先'
    command=Path(__file__).resolve().parents[1]/'scripts/restore_backup.py'
    result=subprocess.run([sys.executable,str(command),str(archive),str(destination)],input=(PASSWORD+'\n').encode(),
                          capture_output=True,env={**os.environ,'PYTHONIOENCODING':'ascii'},timeout=30)
    assert result.returncode==0,result.stderr.decode('ascii',errors='replace')
    assert json.loads(result.stdout)['restored_data']==str(destination/'data')
    assert PASSWORD.encode() not in result.stdout+result.stderr


def test_wrong_password_rejected_before_destination_creation(tmp_path):
    archive,_=backup(tmp_path)
    with pytest.raises(ProtectionError):restore_backup(archive,tmp_path/'wrong','another wrong recovery password')
    assert not (tmp_path/'wrong').exists()


@pytest.mark.parametrize('damage',['truncated','last-tag','header'])
def test_archive_tampering_never_adopts_a_partial_ledger(tmp_path,damage):
    archive,row=backup(tmp_path)
    raw=bytearray(archive.read_bytes())
    if damage=='truncated':raw=raw[:-20]
    elif damage=='last-tag':raw[-1]^=1
    else:raw[len(MAGIC)+4]^=1
    bad=tmp_path/'tampered.ryoshubak';bad.write_bytes(raw)
    failed=tmp_path/'failed'
    with pytest.raises((ValueError,tarfile.TarError,ProtectionError)):restore_backup(bad,failed,PASSWORD)
    if failed.exists():
        assert (failed/'RESTORE_FAILED.txt').is_file()
        with pytest.raises(ValueError,match='復元が完了していない'):ready_store(failed/'data')
    assert ready_store(tmp_path/'original').get(row['id'])['sha256']==row['sha256']


def test_authenticated_but_altered_original_fails_hash_validation(tmp_path):
    archive,_=backup(tmp_path)
    files=backup_files(archive.read_bytes())
    name=next(n for n in files if '/original' in n)
    files[name]=b'altered original'
    bad=tmp_path/'modified.ryoshubak'
    def produce(output):
        with tarfile.open(fileobj=output,mode='w|',format=tarfile.USTAR_FORMAT) as bundle:
            for name,raw in files.items():
                info=tarfile.TarInfo(name);info.size=len(raw);bundle.addfile(info,io.BytesIO(raw))
    with bad.open('wb') as output:write_encrypted(output,PASSWORD,produce)
    with pytest.raises(ValueError,match='原本'):restore_backup(bad,tmp_path/'failed',PASSWORD)
    assert (tmp_path/'failed/RESTORE_FAILED.txt').exists()


@pytest.mark.parametrize('name',['../escape.txt','data/../../escape.txt','data/imports/fake/source.pdf','script.ps1'])
def test_legacy_zip_traversal_unknown_files_fail_before_creation(tmp_path,name):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as bundle:
        bundle.writestr('manifest.json',json.dumps({'app':'Ryoshu','receipts':0}))
        bundle.writestr('data/ledger.sqlite3',b'not a database')
        bundle.writestr(name,b'wrong')
    with pytest.raises(ValueError):restore_backup(archive,tmp_path/'target',PASSWORD)
    assert not (tmp_path/'target').exists() and not (tmp_path/'escape.txt').exists()


def test_legacy_zip_is_encrypted_during_restore_and_preserves_postings(tmp_path):
    legacy=legacy_fixture(tmp_path/'legacy')
    archive=tmp_path/'legacy.zip'
    with zipfile.ZipFile(archive,'w') as bundle:
        bundle.writestr('manifest.json',json.dumps({'app':'Ryoshu','receipts':2}))
        bundle.write(legacy.path,'data/ledger.sqlite3')
        for original in legacy.root.glob('receipts/*/original.pdf'):
            bundle.write(original,'data/'+original.relative_to(legacy.root).as_posix())
    result=restore_backup(archive,tmp_path/'restored',PASSWORD)
    store=ready_store(result['restored_data'])
    assert len(store.list())==2
    assert sum(bool(row['posting']) for row in store.list())==1
    for row in store.list():assert store.keys.read_file(store.folder(row['id'])/'original.pdf').startswith(b'%PDF')
    assert not store.path.read_bytes().startswith(b'SQLite format 3')
