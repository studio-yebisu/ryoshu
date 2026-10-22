"""Exercise the real authorization/setup endpoints without the convenience test client."""
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from receipt_ledger.server import create_app
from receipt_ledger.session import Sessions
from receipt_ledger.protection import ProtectionError
from support import PASSWORD, legacy_fixture

HEADERS={"X-Ledger-Request":"1"}


def sign_in(client,app):
    bootstrap=app.state.sessions.mint_bootstrap()
    response=client.post('/api/session',headers={**HEADERS,'Authorization':'Bootstrap '+bootstrap})
    assert response.status_code==200
    client.headers['Authorization']='Bearer '+response.json()['token']
    return bootstrap


def test_public_health_has_no_paths_models_or_ledger_and_all_data_requires_auth(tmp_path):
    app=create_app(tmp_path,run_workers=False)
    with TestClient(app) as client:
        assert set(client.get('/api/health').json())=={'app','app_version','instance'}
        assert client.get('/').status_code==200
        for path in ['/api/config','/api/receipts','/api/catalog','/api/export/journal','/api/backup','/api/security','/api/receipts/fictional/original']:
            assert client.get(path,headers=HEADERS).status_code==401
        assert client.post('/api/settings',headers=HEADERS,json={'business_context':'unauthorized'}).status_code==401
        assert not (tmp_path/'ledger.enc.sqlite3').exists()
        bootstrap=sign_in(client,app)
        assert client.get('/api/config').status_code==423
        assert client.post('/api/session',headers={**HEADERS,'Authorization':'Bootstrap '+bootstrap}).status_code==401
        result=client.post('/api/security/open',headers=HEADERS,json={'password':PASSWORD,'confirmation':PASSWORD})
        assert result.status_code==200
        assert client.get('/api/config').status_code==200
        assert client.get('/api/health').json()['protected']
        assert 'set-cookie' not in result.headers
        assert client.get('/api/receipts',headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
        assert client.post('/api/settings',headers={**HEADERS,'Origin':'http://127.0.0.1:9999'},json={}).status_code==403
        assert client.get('/api/receipts',headers={'Host':'evil.example'}).status_code==400


def test_bearer_bootstrap_and_wrong_instance_expiry_are_independent(tmp_path,monkeypatch):
    first=Sessions(tmp_path,8766)
    bootstrap=first.mint_bootstrap()
    bearer=first.exchange(bootstrap)
    assert first.authorized('Bearer '+bearer)
    assert not Sessions(tmp_path,8766).authorized('Bearer '+bearer)
    with pytest.raises(ProtectionError):first.exchange(bootstrap)
    with pytest.raises(ProtectionError):Sessions(tmp_path,8766).exchange(bootstrap)
    with pytest.raises(ProtectionError):first.exchange(bootstrap[:-1]+'!')
    old=first.mint_bootstrap()
    import time
    current=time.time()
    monkeypatch.setattr('receipt_ledger.session.time.time',lambda:current+121)
    with pytest.raises(ProtectionError):first.exchange(old)
    monkeypatch.setattr('receipt_ledger.session.time.time',lambda:current+13*3600)
    assert not first.authorized('Bearer '+bearer)


def test_password_validation_never_echoes_input_and_recovery_preserves_data(tmp_path):
    legacy_fixture(tmp_path)
    app=create_app(tmp_path,run_workers=False)
    with TestClient(app) as client:
        sign_in(client,app)
        short='Secret-123'
        bad=client.post('/api/security/open',headers=HEADERS,json={'password':short,'confirmation':short})
        assert bad.status_code==422 and short not in bad.text
        assert client.post('/api/security/open',headers=HEADERS,json={'password':PASSWORD,'confirmation':PASSWORD}).status_code==200
        original=client.get('/api/receipts').json()
    key_path=tmp_path/'.ryoshu-key.dpapi'
    key_path.unlink()
    locked=create_app(tmp_path,run_workers=False)
    with TestClient(locked) as client:
        sign_in(client,locked)
        assert client.get('/api/security').json()['mode']=='locked'
        assert client.get('/api/receipts').status_code==423
        assert client.post('/api/security/open',headers=HEADERS,json={'password':PASSWORD}).status_code==200
        assert client.get('/api/receipts').json()==original
        assert key_path.is_file()


def test_saved_original_preview_db_and_wal_hide_plaintext_and_upload_never_rolls_to_disk(tmp_path,monkeypatch):
    app=create_app(tmp_path,run_workers=False)
    output=io.BytesIO();Image.new('RGB',(40,40),'white').save(output,'PNG')
    raw=output.getvalue()+b'fictional confidential original' + b'X'*(2*1024**2)
    def no_rollover(*args,**kwargs):raise AssertionError('plaintext multipart spool')
    monkeypatch.setattr('tempfile.SpooledTemporaryFile.rollover',no_rollover)
    with TestClient(app) as client:
        sign_in(client,app)
        client.post('/api/security/open',headers=HEADERS,json={'password':PASSWORD,'confirmation':PASSWORD}).raise_for_status()
        response=client.post('/api/receipts',headers=HEADERS,files={'files':('架空.png',raw,'image/png')})
        response.raise_for_status()
        row=app.state.store.get(response.json()['imported'][0]['id'])
        app.state.store.complete(row['id'],{'text':'架空の機密OCRセンチネル','page_count':1},None)
        preview=client.get(f'/api/receipts/{row["id"]}/preview/0')
        preview.raise_for_status()
        assert preview.content.startswith(b'\xff\xd8')
        assert client.get(f'/api/receipts/{row["id"]}/original').content==raw
        for path in tmp_path.rglob('*'):
            if path.is_file() and path.suffix!='.lock':
                content=path.read_bytes()
                assert b'fictional confidential original' not in content
                assert '架空の機密OCRセンチネル'.encode() not in content
        with closing(sqlite3.connect(app.state.store.path)) as unkeyed:
            with pytest.raises(sqlite3.DatabaseError):unkeyed.execute('SELECT * FROM receipts').fetchall()
