"""Favorite accounts persist privately without changing accounting data."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
import threading

from fastapi.testclient import TestClient
import pytest

from receipt_ledger.backups import MAGIC, create_backup
from receipt_ledger.database import fingerprint
from receipt_ledger.importing import Importer
from receipt_ledger.restore import restore_backup
from receipt_ledger.server import create_app
from support import LedgerClient, PASSWORD, ready_app, ready_store
from test_account_categories import previous_catalog
from test_catalog import draft, ready
from test_session_security import sign_in

HEADERS = {'X-Ledger-Request': '1'}


def test_favorites_are_idempotent_persist_and_leave_receipts_and_postings_unchanged(tmp_path):
    store = ready_store(tmp_path)
    row = ready(store)
    posted = store.save(row['id'], row['version'], draft(), confirm=True)
    with store.connect() as db:
        before = fingerprint(db)
        schema = db.execute('PRAGMA user_version').fetchone()[0]
    assert store.favorite_accounts() == []
    assert store.set_account_favorite('E101', True) == ['E101']
    assert store.set_account_favorite('E101', True) == ['E101']
    assert store.set_account_favorite('E102', True) == ['E101', 'E102']
    reopened = ready_store(tmp_path)
    assert reopened.favorite_accounts() == ['E101', 'E102']
    assert reopened.set_account_favorite('E101', False) == ['E102']
    assert reopened.set_account_favorite('E101', False) == ['E102']
    assert reopened.get(row['id']) == posted
    with reopened.connect() as db:
        after = fingerprint(db)
        assert db.execute('PRAGMA user_version').fetchone()[0] == schema == 3
    assert {k: v for k, v in before.items() if k != 'settings'} == {
        k: v for k, v in after.items() if k != 'settings'}


def test_favorites_use_exact_stable_codes_for_legacy_name_collision_and_custom_accounts(tmp_path, monkeypatch):
    import receipt_ledger.store as module
    with monkeypatch.context() as patch:
        patch.setattr(module, 'seed_catalog', previous_catalog)
        store = ready_store(tmp_path)
        legacy = store.add_account('E129', 'expense', '旧版からある独自科目', [], [])
        assert store.set_account_favorite(legacy['code'], True) == [legacy['code']]
    upgraded = ready_store(tmp_path)
    formal = next(a for a in upgraded.accounts() if a['code'] == 'E129')
    assert formal['name'] == '事務用品費'
    assert upgraded.set_account_favorite('E129', True) == ['E129', legacy['code']]
    assert upgraded.set_account_favorite(legacy['code'], False) == ['E129']
    custom = upgraded.add_account('よく使う制作素材費', 'expense', '', [], [], category_id='supplies')
    assert upgraded.set_account_favorite(custom['code'], True) == ['E129', custom['code']]
    with pytest.raises(ValueError):
        upgraded.set_account_favorite(custom['name'], True)


def test_favorites_reject_unknown_alias_inactive_nonselectable_and_payment_accounts(tmp_path):
    store = ready_store(tmp_path)
    store.set_account_favorite('E101', True)
    with store.connect() as db:
        db.execute("UPDATE account_master SET active=0 WHERE code='E102'")
        db.execute("UPDATE account_master SET selectable=0 WHERE code='E103'")
        # Payment and owner accounts remain forbidden even if a corrupt flag says selectable.
        db.execute("UPDATE account_master SET selectable=1 WHERE kind NOT IN ('expense','asset')")
    for code in ['unknown', '消耗品費', '事務用品', 'E102', 'E103', 'B101', 'B102', 'O101', 'O102', 'L101']:
        for favorite in [True, False]:
            with pytest.raises(ValueError):
                store.set_account_favorite(code, favorite)
    for favorite in [1, 0, 'true', None]:
        with pytest.raises(ValueError):
            store.set_account_favorite('E101', favorite)
    assert store.favorite_accounts() == ['E101']


def test_favorites_read_filters_stale_duplicate_and_malformed_values(tmp_path):
    store = ready_store(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE account_master SET active=0 WHERE code='E102'")
        db.execute("INSERT INTO settings VALUES('favorite_accounts',?)", (
            json.dumps(['E101', 'E101', 'E102', 'B102', 'removed', '消耗品費', None, [], {'code': 'E103'}]),))
    assert store.favorite_accounts() == ['E101']
    assert store.set_account_favorite('E103', True) == ['E101', 'E103']
    for value in ['{bad json', '{}', 'null', '"E101"']:
        with store.connect() as db:
            db.execute("UPDATE settings SET value=? WHERE key='favorite_accounts'", (value,))
        assert store.favorite_accounts() == []


def test_concurrent_different_account_updates_do_not_overwrite_each_other(tmp_path):
    store = ready_store(tmp_path)
    codes = ['E101', 'E102', 'E103', 'E104', 'E105', 'E106']
    barrier = threading.Barrier(len(codes))

    def add(code):
        barrier.wait(timeout=10)
        return store.set_account_favorite(code, True)

    with ThreadPoolExecutor(max_workers=len(codes)) as executor:
        list(executor.map(add, codes))
    assert store.favorite_accounts() == codes


def test_favorite_api_requires_authorized_unlocked_same_origin_app(tmp_path):
    app = create_app(tmp_path, run_workers=False)
    path = '/api/accounts/E101/favorite'
    with TestClient(app) as client:
        assert client.post(path, json={'favorite': True}, headers=HEADERS).status_code == 401
        sign_in(client, app)
        assert client.post(path, json={'favorite': True}, headers=HEADERS).status_code == 423
        client.post('/api/security/open', json={'password': PASSWORD, 'confirmation': PASSWORD}, headers=HEADERS).raise_for_status()
        assert client.get('/api/config').json()['favorite_accounts'] == []
        assert client.post(path, json={'favorite': True}).status_code == 403
        assert client.post(path, json={'favorite': True}, headers={**HEADERS, 'Origin': 'https://evil.example'}).status_code == 403
        assert client.post(path, json={'favorite': True}, headers={**HEADERS, 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        response = client.post(path, json={'favorite': True}, headers=HEADERS)
        assert response.status_code == 200 and response.json() == {'favorite_accounts': ['E101']}


def test_favorite_api_strict_body_config_and_custom_account(tmp_path):
    app = ready_app(tmp_path, run_workers=False)
    with LedgerClient(app) as client:
        path = '/api/accounts/E101/favorite'
        invalid = [{}, {'favorite': None}, {'favorite': 0}, {'favorite': 1}, {'favorite': 'true'},
                   {'favorite': 'false'}, {'favorite': []}, {'favorite': {}},
                   {'favorite': True, 'other': 'unexpected'}, [], None]
        for body in invalid:
            assert client.post(path, json=body, headers=HEADERS).status_code == 422
        assert client.get('/api/config').json()['favorite_accounts'] == []
        assert client.post('/api/accounts/unknown/favorite', json={'favorite': True}, headers=HEADERS).status_code == 422
        assert client.post('/api/accounts/B102/favorite', json={'favorite': True}, headers=HEADERS).status_code == 422
        custom = client.post('/api/accounts', json={'name': '制作素材お気に入り費'}, headers=HEADERS).json()
        custom_path = f"/api/accounts/{custom['code']}/favorite"
        for _ in range(2):
            assert client.post(custom_path, json={'favorite': True}, headers=HEADERS).json() == {'favorite_accounts': [custom['code']]}
        assert client.get('/api/config').json()['favorite_accounts'] == [custom['code']]
        assert client.post(custom_path, json={'favorite': False}, headers=HEADERS).json() == {'favorite_accounts': []}


def test_encrypted_backup_restore_preserves_favorites_and_postings(tmp_path):
    store = ready_store(tmp_path / 'data')
    row = ready(store)
    posted = store.save(row['id'], row['version'], draft(), confirm=True)
    custom = store.add_account('秘密の架空よく使う科目', 'expense', '', [], [])
    store.set_account_favorite('E101', True)
    store.set_account_favorite(custom['code'], True)
    temporary, archive = create_backup(store, Importer(store), PASSWORD)
    try:
        raw = archive.read_bytes()
        assert raw.startswith(MAGIC)
        assert b'favorite_accounts' not in raw and custom['name'].encode() not in raw
        result = restore_backup(archive, tmp_path / 'restored', PASSWORD)
    finally:
        temporary.cleanup()
    restored = ready_store(result['restored_data'])
    assert restored.favorite_accounts() == ['E101', custom['code']]
    assert restored.get(row['id']) == posted
    assert b'favorite_accounts' not in restored.path.read_bytes()
    with closing(sqlite3.connect(restored.path)) as db:
        with pytest.raises(sqlite3.DatabaseError):
            db.execute('SELECT * FROM settings').fetchall()
