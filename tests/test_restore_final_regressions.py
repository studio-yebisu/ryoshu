"""Review regressions at restore adoption and migration crash boundaries."""
from contextlib import contextmanager
import hashlib
import io
import json
import struct
import zipfile

import pytest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from receipt_ledger.backups import MAGIC, create_backup, read_encrypted, read_header, write_encrypted
from receipt_ledger.database import CipherDatabase
from receipt_ledger.importing import Importer
from receipt_ledger.protection import LedgerKeys, ProtectionError, _json
from receipt_ledger.restore import restore_backup
from support import PASSWORD, legacy_fixture, ready_store


def encrypted_backup(root, count=1, missing=False):
    store=ready_store(root)
    for index in range(count):
        row,_=store.add(f'fictional-{index}.png','.png',f'fictional original {index}'.encode())
        if missing:(store.folder(row['id'])/'original.png').unlink()
    temporary,archive=create_backup(store,Importer(store),PASSWORD,allow_missing=missing)
    return temporary,archive


def legacy_backup(root):
    legacy=legacy_fixture(root/'legacy')
    archive=root/'legacy.zip'
    with zipfile.ZipFile(archive,'w') as bundle:
        bundle.writestr('manifest.json',json.dumps({'app':'Ryoshu','receipts':2}))
        bundle.write(legacy.path,'data/ledger.sqlite3')
        for original in legacy.root.glob('receipts/*/original.pdf'):
            bundle.write(original,'data/'+original.relative_to(legacy.root).as_posix())
    return archive


def test_rescue_of_300_missing_originals_reopens_with_bounded_authenticated_state(tmp_path):
    temporary,archive=encrypted_backup(tmp_path/'original',300,missing=True)
    try:
        result=restore_backup(archive,tmp_path/'restored',PASSWORD)
    finally:
        temporary.cleanup()
    assert result['receipts']==result['missing_files']==300
    restored=ready_store(result['restored_data'])
    assert restored.ready and len(restored.list())==300
    assert restored.storage_issues()['missing_originals']==300
    state=restored.database._state()
    assert state['phase']=='active' and state['missing_file_count']==300
    assert 'missing_files' not in state
    assert restored.database.state_path.stat().st_size<16*1024
    assert not (tmp_path/'restored/RESTORE_IN_PROGRESS.txt').exists()


def test_missing_state_with_both_plain_and_ciphertext_never_overwrites_either(tmp_path):
    legacy=legacy_fixture(tmp_path)
    keys=LedgerKeys(tmp_path);keys.setup(PASSWORD)
    cipher=tmp_path/'ledger.enc.sqlite3';cipher.write_bytes(b'existing encrypted book sentinel')
    before={p:hashlib.sha256(p.read_bytes()).digest() for p in [legacy.path,cipher]}
    with pytest.raises(ProtectionError,match='同時'):
        CipherDatabase(tmp_path,keys)
    assert all(hashlib.sha256(p.read_bytes()).digest()==digest for p,digest in before.items())
    assert not (tmp_path/'.ryoshu-vault.json').exists()


def test_creating_state_after_guard_commit_finishes_active_on_restart(tmp_path,monkeypatch):
    keys=LedgerKeys(tmp_path);keys.setup(PASSWORD)
    save=CipherDatabase._save_state
    def fail_active(self,phase,**details):
        if phase=='active':raise OSError('simulated stop after new guard')
        return save(self,phase,**details)
    monkeypatch.setattr(CipherDatabase,'_save_state',fail_active)
    with pytest.raises(OSError):CipherDatabase(tmp_path,keys)
    monkeypatch.setattr(CipherDatabase,'_save_state',save)
    resumed=CipherDatabase(tmp_path,LedgerKeys(tmp_path))
    assert resumed._state()['phase']=='active'


def test_guard_alone_never_offers_setup_or_creates_replacement_keys(tmp_path):
    keys=LedgerKeys(tmp_path);keys.setup(PASSWORD)
    database=CipherDatabase(tmp_path,keys)
    for path in [keys.key_path,keys.recovery_path,database.path,database.state_path]:path.unlink()
    locked=LedgerKeys(tmp_path)
    assert locked.mode=='locked'
    with pytest.raises(ProtectionError,match='既存'):locked.setup(PASSWORD)
    assert not keys.key_path.exists() and not keys.recovery_path.exists()


def test_deep_protection_json_reports_value_error():
    with pytest.raises(ValueError,match='階層'):_json('['*10000+'0'+']'*10000)


def test_state_limit_rejects_oversize_update_without_replacing_valid_state(tmp_path):
    keys=LedgerKeys(tmp_path);keys.setup(PASSWORD)
    database=CipherDatabase(tmp_path,keys)
    previous=database.state_path.read_bytes()
    with pytest.raises(ProtectionError,match='大きすぎ'):
        database._save_state('active',unexpected_large_detail='x'*(16*1024))
    assert database.state_path.read_bytes()==previous
    assert CipherDatabase(tmp_path,LedgerKeys(tmp_path))._state()['phase']=='active'


def test_stream_cipher_interoperates_with_standard_high_level_aesgcm():
    plaintext=b'fictional backup interoperability\0'*3000
    output=io.BytesIO()
    write_encrypted(output,PASSWORD,lambda target:target.write(plaintext))
    raw=output.getvalue()
    key,nonce,header=read_header(io.BytesIO(raw),PASSWORD)
    assert AESGCM(key).decrypt(nonce,raw[len(header):],header)==plaintext
    independent=header+AESGCM(key).encrypt(nonce,plaintext+b' independent',header)
    result=read_encrypted(io.BytesIO(independent),PASSWORD,lambda source:source.read(len(plaintext)+12))
    assert result==plaintext+b' independent'
    # A malformed tag is rejected before the archive consumer sees any plaintext.
    calls=[]
    damaged=independent[:-1]+bytes([independent[-1]^1])
    with pytest.raises(ProtectionError):
        read_encrypted(io.BytesIO(damaged),PASSWORD,lambda source:calls.append(True))
    assert calls==[]
    # Valid JSON whitespace leaves the password wrapper valid, but changes the
    # exact header authenticated by payload GCM. Parsing alone must not accept it.
    payload=header[len(MAGIC)+4:]+b' '
    changed_header=MAGIC+struct.pack('>I',len(payload))+payload
    with pytest.raises(ProtectionError):
        read_encrypted(io.BytesIO(changed_header+raw[len(header):]),PASSWORD,lambda source:calls.append(True))
    assert calls==[]


def test_encrypted_restore_destination_created_after_preflight_is_left_untouched(tmp_path,monkeypatch):
    from receipt_ledger import restore_encrypted as module
    temporary,archive=encrypted_backup(tmp_path/'original')
    destination=tmp_path/'collision'
    actual=module.read_encrypted
    def collide(source,password,consume,**kwargs):
        destination.mkdir();(destination/'keep.txt').write_text('other restore owns this','utf-8')
        return actual(source,password,consume,**kwargs)
    monkeypatch.setattr(module,'read_encrypted',collide)
    try:
        with pytest.raises(ValueError,match='存在'):restore_backup(archive,destination,PASSWORD)
    finally:temporary.cleanup()
    assert [p.name for p in destination.iterdir()]==['keep.txt']


def test_legacy_restore_destination_created_after_preflight_is_left_untouched(tmp_path,monkeypatch):
    from receipt_ledger import protection
    archive=legacy_backup(tmp_path)
    destination=tmp_path/'collision'
    actual=protection.check_password
    def collide(password):
        actual(password)
        destination.mkdir();(destination/'keep.txt').write_text('other restore owns this','utf-8')
    monkeypatch.setattr(protection,'check_password',collide)
    with pytest.raises(ValueError,match='存在'):restore_backup(archive,destination,PASSWORD)
    assert [p.name for p in destination.iterdir()]==['keep.txt']


def test_legacy_cleanup_failure_never_removes_in_progress_marker(tmp_path,monkeypatch):
    from receipt_ledger import restore as module
    archive=legacy_backup(tmp_path)
    actual=module.private_work
    @contextmanager
    def failed_cleanup(root):
        with actual(root) as folder:
            yield folder
        raise ProtectionError('simulated cleanup failure')
    monkeypatch.setattr(module,'private_work',failed_cleanup)
    destination=tmp_path/'failed'
    with pytest.raises(ProtectionError,match='cleanup'):restore_backup(archive,destination,PASSWORD)
    assert (destination/'RESTORE_IN_PROGRESS.txt').exists()
    assert (destination/'RESTORE_FAILED.txt').exists()
    with pytest.raises(ValueError,match='復元が完了していない'):ready_store(destination/'data')


def test_encrypted_restore_checks_fresh_key_loader_before_removing_marker(tmp_path,monkeypatch):
    from receipt_ledger import restore_encrypted as module
    temporary,archive=encrypted_backup(tmp_path/'original')
    actual=module.LedgerKeys
    calls=[]
    def fail_reopen(root):
        calls.append(root)
        if len(calls)>1:raise ProtectionError('fresh key validation failed')
        return actual(root)
    monkeypatch.setattr(module,'LedgerKeys',fail_reopen)
    destination=tmp_path/'failed'
    try:
        with pytest.raises(ProtectionError,match='fresh key'):restore_backup(archive,destination,PASSWORD)
    finally:temporary.cleanup()
    assert len(calls)==2
    assert (destination/'RESTORE_IN_PROGRESS.txt').exists() and (destination/'RESTORE_FAILED.txt').exists()
