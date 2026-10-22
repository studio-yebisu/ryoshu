"""Account choice, preserved journals and schema-three backup boundaries."""
from collections import Counter
from contextlib import closing
import csv
import io
import json
from pathlib import Path
import zipfile

import pytest

from receipt_ledger.accounting import summary
from receipt_ledger.backups import create_backup
from receipt_ledger.catalog import account_categories, account_key, default_accounts, resolve_account, seed_catalog
from receipt_ledger.exports import build_export
from receipt_ledger.importing import Importer
from receipt_ledger.restore import restore_backup
from receipt_ledger.store import Conflict
from support import LedgerClient, PASSWORD, ready_app, ready_store
from test_catalog import draft, ready

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {'X-Ledger-Request':'1'}


def previous_catalog():
    return json.loads((ROOT/'data/catalog-history/2026-10-22.2.json').read_text('utf-8'))


def make_v2(store):
    """Remove only the category extension to model the real v1.2 DB layout."""
    with store.connect() as db:
        db.execute('ALTER TABLE account_master DROP COLUMN category_id')
        db.execute('PRAGMA user_version=2')


def legacy_archive(store, directory, version):
    """Create the explicitly plaintext, historical format using fictional rows only."""
    plain = directory/'legacy.sqlite3'
    with store.connect() as db:
        db.execute("ATTACH DATABASE ? AS legacy_plain KEY ''", (str(plain),))
        db.execute("SELECT sqlcipher_export('legacy_plain')")
        db.execute(f'PRAGMA legacy_plain.user_version={int(version)}')
        db.execute('DETACH DATABASE legacy_plain')
    archive=directory/'legacy.zip'
    with zipfile.ZipFile(archive,'w') as bundle:
        bundle.writestr('manifest.json',json.dumps({'app':'Ryoshu','receipts':len(store.list())}))
        bundle.write(plain,'data/ledger.sqlite3')
        for original in (store.root/'receipts').glob('*/original.pdf'):
            bundle.writestr('data/'+original.relative_to(store.root).as_posix(),store.keys.read_file(original))
    return archive


def test_seed_has_distinct_formal_accounts_complete_categories_and_only_intended_alias_overlap():
    seed = seed_catalog()
    old = previous_catalog()
    assert len(seed['accounts'])==74 and len(default_accounts())==69
    assert Counter(a['kind'] for a in seed['accounts'])=={'expense':49,'asset':20,'cash':1,'bank':1,'liability':1,'equity':2}
    assert len({account_key(a['name']) for a in seed['accounts']})==74
    assert len({account_key(a['code']) for a in seed['accounts']})==74
    categories = {c['id']:c for c in account_categories()}
    assert len(categories)==14
    for account in seed['accounts']:
        assert account['kind'] in categories[account['category_id']]['kinds']
        if account['selectable']:
            assert account['kind'] in {'expense','asset'}
    by_code = {a['code']:a for a in seed['accounts']}
    for account in old['accounts']:
        for key in ['name','code','kind','aliases']:
            assert by_code[account['code']][key]==account[key]
    names = {account_key(a['name']) for a in seed['accounts']}
    overlaps = {alias for a in old['accounts'] for alias in a['aliases'] if account_key(alias) in names}
    assert overlaps=={'事務用品費','販売促進費','外注費','支払利息','リース料'}


@pytest.mark.parametrize('formal,code,legacy',[('事務用品費','E129','E101'),('販売促進費','E130','E106'),
    ('外注費','E131','E111'),('支払利息','E132','E123'),('リース料','E133','E118')])
def test_explicit_formal_names_win_over_legacy_aliases(formal,code,legacy):
    accounts = default_accounts()
    assert resolve_account(formal,accounts)['code']==code
    assert resolve_account(code,accounts)['name']==formal
    assert resolve_account(legacy,accounts)['code']==legacy
    assert resolve_account('Ｅ１０１',accounts)['name']=='消耗品費'


def test_v2_upgrade_preserves_custom_collision_manual_fields_and_posted_journal(tmp_path,monkeypatch):
    import receipt_ledger.store as module
    with monkeypatch.context() as patch:
        patch.setattr(module,'seed_catalog',previous_catalog)
        store = ready_store(tmp_path)
        custom = store.add_account('取材費','expense','自分で定義した取材の記録',['取材調査代'],['独自の確認文'])
        saved = ready(store)
        saved = store.save(saved['id'],saved['version'],draft(account='取材費'),confirm=True)
        with store.connect() as db:
            db.execute("UPDATE account_master SET description='自分の文具の説明',hints_json=?,questions_json=? WHERE code='E101'",
                (json.dumps({'any':['独自の文具'],'unless_any':[]}),json.dumps(['独自の質問'])))
        make_v2(store)
    upgraded = ready_store(tmp_path)
    assert upgraded.get(saved['id'])['posting']==saved['posting']
    assert upgraded.get(saved['id'])['draft']==saved['draft']
    accounts = {a['code']:a for a in upgraded.accounts(False)}
    assert accounts['E101']['category_id']=='supplies'
    assert accounts['E101']['description']=='自分の文具の説明'
    assert accounts['E101']['hints']=={'any':['独自の文具'],'unless_any':[]}
    assert accounts['E101']['review_questions']==['独自の質問']
    assert accounts[custom['code']]['category_id']=='other'
    assert accounts[custom['code']]['description']=='自分で定義した取材の記録'
    assert resolve_account('取材費',list(accounts.values()))['code']==custom['code']
    assert 'E138' not in accounts  # New default with the same name never replaces user data.
    assert len(accounts)==74
    assert any(s['id']=='freee_account_items' for s in upgraded.catalog()['sources'])
    backups = list((tmp_path/'backups').glob('before-account-categories-v3-*.sqlite3'))
    assert len(backups)==1
    with closing(upgraded.database.open(backups[0],readonly=True)) as snapshot:
        assert snapshot.execute('PRAGMA user_version').fetchone()[0]==2
        assert 'category_id' not in {r['name'] for r in snapshot.execute('PRAGMA table_info(account_master)')}
        assert snapshot.execute('SELECT status FROM journal_entries WHERE receipt_id=?',(saved['id'],)).fetchone()[0]=='posted'
    ready_store(tmp_path)
    assert len(list((tmp_path/'backups').glob('before-account-categories-v3-*.sqlite3')))==1


@pytest.mark.parametrize('name,kind,new_formal',[('E129','expense','事務用品費'),('A106','asset','一括償却資産')])
def test_v2_custom_name_matching_new_seed_code_keeps_identity_when_saved_and_reopened(tmp_path,monkeypatch,name,kind,new_formal):
    import receipt_ledger.store as module
    with monkeypatch.context() as patch:
        patch.setattr(module,'seed_catalog',previous_catalog)
        store=ready_store(tmp_path)
        custom=store.add_account(name,kind,'以前から使う独自科目',[],[])
        row=ready(store)
        row=store.save(row['id'],row['version'],draft(account=name),confirm=True)
        old_posting=row['posting']
        assert any(line['account_code']==custom['code'] for line in old_posting['lines'])
        make_v2(store)
    upgraded=ready_store(tmp_path)
    assert upgraded.get(row['id'])['posting']==old_posting
    assert upgraded.get(row['id'])['draft']['account']==name
    assert resolve_account(name,upgraded.accounts())['code']==custom['code']
    assert resolve_account(new_formal,upgraded.accounts())['code']==name
    saved=upgraded.save(row['id'],row['version'],draft(account=name,description='独自科目のまま説明だけ変更'),confirm=True)
    assert saved['draft']['account']==name and saved['account_code']==custom['code']
    assert any(line['account_code']==custom['code'] for line in saved['posting']['lines'])
    assert not any(line['account_code']==name for line in saved['posting']['lines'])
    reopened=ready_store(tmp_path)
    assert reopened.get(row['id'])['posting']==saved['posting']
    assert reopened.get(row['id'])['account_code']==custom['code']
    with reopened.connect() as db:
        assert db.execute('SELECT status FROM journal_entries WHERE id=?',(old_posting['id'],)).fetchone()[0]=='void'
        assert {r['account_code'] for r in db.execute('SELECT account_code FROM journal_lines WHERE entry_id=?',(old_posting['id'],))}=={custom['code'],'O102','B102'}


def test_seed_upgrade_and_reopen_preserve_manually_assigned_category(tmp_path,monkeypatch):
    import receipt_ledger.store as module
    with monkeypatch.context() as patch:
        patch.setattr(module,'seed_catalog',previous_catalog)
        store=ready_store(tmp_path)
        with store.connect() as db:
            db.execute("UPDATE account_master SET category_id='other' WHERE code='E101'")
    upgraded=ready_store(tmp_path)
    assert next(a for a in upgraded.accounts() if a['code']=='E101')['category_id']=='other'
    assert next(a for a in ready_store(tmp_path).accounts() if a['code']=='E101')['category_id']=='other'


def test_category_api_validation_custom_default_and_nonexpense_kind_guard(tmp_path):
    app = ready_app(tmp_path,run_workers=False)
    with LedgerClient(app) as client:
        config = client.get('/api/config').json()
        catalog = client.get('/api/catalog').json()
        assert config['account_categories']==catalog['account_categories']
        assert len(config['accounts'])==69 and len(catalog['accounts'])==74
        assert client.get('/api/health').json()['schema_version']==3
        body={'name':'撮影素材利用料','kind':'expense','category_id':'learning'}
        result=client.post('/api/accounts',json=body,headers=HEADERS)
        assert result.status_code==200 and result.json()['category_id']=='learning'
        result=client.post('/api/accounts',json={'name':'自分用資産','kind':'asset'},headers=HEADERS)
        assert result.status_code==200 and result.json()['category_id']=='assets'
        for category in ['missing','assets','settlement']:
            result=client.post('/api/accounts',json={**body,'name':'拒否される科目','category_id':category},headers=HEADERS)
            assert result.status_code==422
        result=client.post('/api/accounts',json={**body,'category_id':123},headers=HEADERS)
        assert result.status_code==422
        # Even a corrupted selectable flag cannot expose bank/equity as a receipt expense.
        with app.state.store.connect() as db:
            db.execute("UPDATE account_master SET selectable=1 WHERE code IN ('B102','O101')")
        assert not {'普通預金','事業主借'} & {a['name'] for a in client.get('/api/config').json()['accounts']}
        row=ready(app.state.store)
        with pytest.raises(ValueError):
            app.state.store.save(row['id'],row['version'],draft(account='普通預金'),confirm=True)


@pytest.mark.parametrize('name,category,kind',[('事務用品費','supplies','expense'),('肥料費','agriculture','expense'),('敷金','assets','asset')])
def test_new_account_save_reopen_change_void_and_four_csv_profiles(tmp_path,name,category,kind):
    store=ready_store(tmp_path)
    row=ready(store)
    row=store.save(row['id'],row['version'],draft(business_percent=100),confirm=True)
    before=dict(row['draft'])
    changed=store.save(row['id'],row['version'],draft(account=name,business_percent=100),confirm=False)
    assert changed['status']=='review' and changed['posting'] is None
    assert {k:v for k,v in changed['draft'].items() if k!='account'}=={k:v for k,v in before.items() if k!='account'}
    with pytest.raises(Conflict):
        store.save(row['id'],row['version'],draft(account=name),confirm=False)
    changed=store.save(changed['id'],changed['version'],draft(account=name,business_percent=100),confirm=True)
    restored=ready_store(tmp_path)
    current=restored.get(row['id'])
    assert current['account_kind']==kind and current['draft']['account']==name
    assert resolve_account(name,restored.accounts())['category_id']==category
    assert summary(restored.list())['asset_total']==(1650 if kind=='asset' else 0)
    with restored.connect() as db:
        assert db.execute("SELECT count(*) FROM journal_entries WHERE status='void'").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM journal_entries WHERE status='posted'").fetchone()[0]==1
    for profile in ['generic','freee','mf','yayoi']:
        exported=build_export(restored.list(),profile=profile,kind='journal',tax_mode='exempt')
        assert not exported.issues
        rows=list(csv.reader(io.StringIO(exported.content().decode(exported.encoding))))
        assert any(name in row for row in rows)
        assert exported.receipt_count==1


@pytest.mark.parametrize('version',[2,3])
def test_encrypted_backup_restores_v2_migrates_and_v3_roundtrips_categories(tmp_path,version):
    store=ready_store(tmp_path/'source')
    account=store.add_account('独自素材費','expense','独自の支出',['素材の代金'],[],category_id='supplies')
    row=ready(store)
    row=store.save(row['id'],row['version'],draft(account=account['name']),confirm=True)
    if version==2:
        make_v2(store)
    temporary,archive=create_backup(store,Importer(store),PASSWORD)
    try:
        result=restore_backup(archive,tmp_path/'restored',PASSWORD)
    finally:
        temporary.cleanup()
    restored=ready_store(result['restored_data'])
    assert restored.catalog()['schema_version']==3
    assert restored.get(row['id'])['draft']==row['draft']
    assert restored.get(row['id'])['posting']==row['posting']
    found=resolve_account(account['name'],restored.accounts())
    assert found['code']==account['code']
    assert found['category_id']==('other' if version==2 else 'supplies')


@pytest.mark.parametrize('kind',['encrypted','legacy'])
def test_restore_and_store_reject_future_schema_four(tmp_path,kind):
    store=ready_store(tmp_path/'source')
    ready(store)
    with store.connect() as db:
        db.execute('PRAGMA user_version=4')
    with pytest.raises(RuntimeError,match='新しいデータベース'):
        ready_store(store.root)
    if kind=='encrypted':
        temporary,archive=create_backup(store,Importer(store),PASSWORD)
    else:
        temporary=None
        archive=legacy_archive(store,tmp_path,4)
    try:
        with pytest.raises(ValueError,match='版|整合性'):
            restore_backup(archive,tmp_path/'rejected',PASSWORD)
    finally:
        if temporary:temporary.cleanup()
    assert (tmp_path/'rejected/RESTORE_FAILED.txt').is_file()
    with store.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==4


def test_legacy_format_accepts_schema_three_and_preserves_category_and_posting(tmp_path):
    store=ready_store(tmp_path/'source')
    custom=store.add_account('独自の広告制作費','expense','独自の出費',[],[],category_id='sales')
    row=ready(store)
    row=store.save(row['id'],row['version'],draft(account=custom['name']),confirm=True)
    archive=legacy_archive(store,tmp_path,3)
    result=restore_backup(archive,tmp_path/'restored',PASSWORD)
    restored=ready_store(result['restored_data'])
    assert restored.get(row['id'])['posting']==row['posting']
    assert resolve_account(custom['name'],restored.accounts())['category_id']=='sales'
    assert restored.catalog()['schema_version']==3
