import io
import json
import sqlite3
import zipfile

import pytest
from support import LedgerClient as TestClient

from receipt_ledger.accounting import csv_export, summary
from receipt_ledger.catalog import apply_account_policies, default_accounts, retrieve_accounts
from receipt_ledger.pipeline import ground,row_ordered_text,money_in
from test_ledger import candidate
from receipt_ledger.schemas import Draft
from support import ready_app as create_app
from support import ready_store as Store


def ready(store, text="合計 1650円"):
    row,_=store.add("fictional.pdf",".pdf",b"fictional-test-original")
    store.complete(row["id"],{"text":text,"pages":[],"page_count":1},None)
    return store.get(row["id"])


def draft(**changes):
    values={"date":"2026-10-20","merchant":"架空取引先","total":1650,"account":"消耗品費","payment":"bank","business_percent":50,"purpose":"検証用の業務支出","description":"事務用品"}
    return Draft(**(values|changes))


def test_master_is_persistent_and_user_definitions_are_not_reseeded(tmp_path):
    store=Store(tmp_path)
    assert len(store.accounts(False))==38
    assert len(store.accounts())==33
    assert len(store.policies())==15
    with store.connect() as db:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0]==1
        db.execute("UPDATE account_master SET description='手動で決めた独自の説明' WHERE code='E101'")
    reopened=Store(tmp_path)
    assert next(a for a in reopened.accounts() if a["code"]=="E101")["description"]=="手動で決めた独自の説明"
    assert reopened.catalog()["schema_version"]==2


def test_seed_upgrade_preserves_manual_fields_and_existing_journal(tmp_path,monkeypatch):
    import receipt_ledger.store as module
    from receipt_ledger.catalog import seed_catalog
    from pathlib import Path
    old=json.loads((Path(__file__).parents[1]/'data/catalog-history/2026-10-21.2.json').read_text(encoding='utf8'))
    with monkeypatch.context() as patch:
        patch.setattr(module,'seed_catalog',lambda:old)
        store=Store(tmp_path);r=ready(store);r=store.save(r['id'],r['version'],draft(),confirm=True)
        with store.connect() as db:
            db.execute("UPDATE account_master SET description='独自の説明',hints_json=? WHERE code='E101'",(json.dumps({'any':['特注品'],'unless_any':[]}),))
            db.execute("UPDATE account_policies SET message='独自の確認文' WHERE code='P_REFUND'")
    upgraded=Store(tmp_path)
    a=next(a for a in upgraded.accounts() if a['code']=='E101')
    assert a['description']=='独自の説明' and a['hints']['any']==['特注品']
    refund=next(p for p in upgraded.policies() if p['code']=='P_REFUND')
    assert refund['message']=='独自の確認文' and refund['match']=={'document_flag':'refund'}
    assert upgraded.get(r['id'])['posting']==r['posting']
    assert len(upgraded.accounts(False))==38 and len(upgraded.policies())==15


def test_posting_is_balanced_normalized_and_foreign_keys_are_enforced(tmp_path):
    store=Store(tmp_path);row=ready(store)
    row=store.save(row["id"],row["version"],draft(),confirm=True)
    lines=row["posting"]["lines"]
    assert len(lines)==3
    assert sum(l["amount"] for l in lines if l["side"]=="debit")==1650
    assert sum(l["amount"] for l in lines if l["side"]=="credit")==1650
    assert {l["account_code"] for l in lines}=={"E101","O102","B102"}
    with store.connect() as db:
        with pytest.raises(__import__("sqlcipher3").IntegrityError):
            db.execute("INSERT INTO journal_lines VALUES(?,99,'UNKNOWN','debit',1)",(row["posting"]["id"],))


def test_changing_confirmed_data_voids_posting_and_preserves_history(tmp_path):
    store=Store(tmp_path);row=ready(store)
    row=store.save(row["id"],row["version"],draft(),confirm=True)
    row=store.save(row["id"],row["version"],draft(total=2200),confirm=False)
    assert row["posting"] is None and row["status"]=="review"
    assert summary(store.list())["count"]==0
    row=store.save(row["id"],row["version"],draft(total=2200),confirm=True)
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM journal_entries WHERE status='void'").fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM journal_entries WHERE status='posted'").fetchone()[0]==1
    assert b"1100" in csv_export(store.list(),kind="journal")


def test_custom_asset_and_alias_are_used_by_validation_and_totals(tmp_path):
    store=Store(tmp_path)
    custom=store.add_account("制作用ライセンス","asset","買切りの制作用ライセンス",["買切ライセンス"],["償却の扱い"])
    row=ready(store)
    row=store.save(row["id"],row["version"],draft(account="買切ライセンス",business_percent=100),confirm=True)
    assert row["draft"]["account"]==custom["name"]
    assert row["account_code"]==custom["code"]
    assert summary(store.list())["asset_total"]==1650
    assert summary(store.list())["expense_total"]==0
    assert Store(tmp_path).get(row["id"])["account_code"]==custom["code"]
    with pytest.raises(ValueError):
        store.add_account("別の科目","expense","",["消耗品費"],[])


def test_nonselectable_or_unknown_accounts_cannot_be_used_as_expense(tmp_path):
    store=Store(tmp_path);row=ready(store)
    with pytest.raises(ValueError):
        store.save(row["id"],row["version"],draft(account="普通預金"),confirm=True)
    with pytest.raises(ValueError):
        store.save(row["id"],row["version"],draft(account="未登録の科目"),confirm=True)


def test_refund_guard_cannot_be_bypassed_by_manually_positive_amount(tmp_path):
    store=Store(tmp_path);row=ready(store,"返金明細書\n返品\n返金合計 -1650円")
    with pytest.raises(ValueError,match="返金"):
        store.save(row["id"],row["version"],draft(total=1650),confirm=True)
    assert store.get(row["id"])["posting"] is None


def test_food_guard_applies_even_if_a_merchant_suggestion_is_present():
    value={"account":"消耗品費","merchant":"架空カフェ","description":"ランチ","total":1000,"warnings":[],"reason":"過去の店舗ルール"}
    checked=apply_account_policies(value,"架空カフェ\nランチ 1000円")
    assert checked["account"]==""
    assert any(p["code"]=="P_FOOD_CONTEXT" for p in checked["policy_matches"])


def test_v1_migration_backs_up_and_preserves_confirmed_data_and_rules(tmp_path):
    path=tmp_path/"ledger.sqlite3"
    value=draft().model_dump()
    db=sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE receipts(id TEXT PRIMARY KEY,sha256 TEXT UNIQUE NOT NULL,original_name TEXT NOT NULL,extension TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,status TEXT NOT NULL,stage TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,draft_json TEXT NOT NULL,ocr_json TEXT,suggestion_json TEXT,error TEXT NOT NULL DEFAULT '');
        CREATE TABLE audit(seq INTEGER PRIMARY KEY AUTOINCREMENT,receipt_id TEXT NOT NULL,at TEXT NOT NULL,action TEXT NOT NULL,detail_json TEXT NOT NULL);
        CREATE TABLE merchant_rules(merchant TEXT PRIMARY KEY,account TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        PRAGMA user_version=1;
    """)
    raw_original=b'fictional legacy original'
    import hashlib
    digest=hashlib.sha256(raw_original).hexdigest()
    folder=tmp_path/'receipts/legacy';folder.mkdir(parents=True);(folder/'original.pdf').write_bytes(raw_original)
    db.execute("INSERT INTO receipts(id,sha256,original_name,extension,created_at,updated_at,status,draft_json) VALUES('legacy',?,'keep.pdf','.pdf','2026-10-20','2026-10-20','confirmed',?)",(digest,json.dumps(value,ensure_ascii=False)))
    db.execute("INSERT INTO merchant_rules VALUES('架空取引先','消耗品費','2026-10-20')")
    db.execute("INSERT INTO settings VALUES('business_context','既存の事業内容')")
    db.commit();db.close()
    store=Store(tmp_path)
    migrated=store.get("legacy")
    assert migrated["draft"]==value and migrated["sha256"]==digest
    assert migrated["posting"] and migrated["status"]=="confirmed"
    assert store.rules()[0]["account_code"]=="E101"
    assert store.business_context()=="既存の事業内容"
    backups=list((tmp_path/"backups").glob("before-catalog-v2-*.sqlite3"))
    assert len(backups)==1
    connection=store.database.open(backups[0],readonly=True)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0]==1
        assert connection.execute("SELECT draft_json FROM receipts").fetchone()[0]==json.dumps(value,ensure_ascii=False)
    finally:connection.close()
    Store(tmp_path)
    assert len(list((tmp_path/"backups").glob("before-catalog-v2-*.sqlite3")))==1


def test_catalog_api_updates_selection_and_samples_are_downloadable(tmp_path):
    with TestClient(create_app(tmp_path,run_workers=False)) as client:
        assert len(client.get("/api/catalog").json()["accounts"])==38
        response=client.post("/api/accounts",json={"name":"制作素材費","kind":"expense","description":"素材の当月購入","aliases":["素材費"],"review_questions":["業務用途"],"keywords":["制作素材ライセンス"]},headers={"X-Ledger-Request":"1"})
        assert response.status_code==200
        assert response.json()["hints"]["any"]==["制作素材ライセンス"]
        assert any(a["name"]=="制作素材費" for a in client.get("/api/config").json()["accounts"])
        assert client.get("/api/config").json()["limits"]["max_files"]>=30
        response=client.get("/api/samples/zip")
        assert response.status_code==200
        sample_count=client.get("/api/config").json()["samples"]["count"]
        assert sample_count == 100
        with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
            assert len([n for n in bundle.namelist() if n.endswith('.pdf')])==sample_count
        assert client.get("/api/samples/review").headers["content-disposition"].startswith("inline")
        assert "Ryoshu" in client.get("/evidence/").text
        assert client.get("/evidence/results.js").status_code == 200
        assert client.get("/samples/receipts-100-v2/gallery.html").status_code == 200
        assert client.get("/samples/receipts-100-v2/individual/V001.pdf").content.startswith(b"%PDF")
        assert client.get("/samples/receipts-100-v2/thumbnails/V001.jpg").headers["content-type"].startswith("image/jpeg")


def test_retrieval_uses_purchase_terms_and_excludes_repair_from_asset_hint():
    accounts=default_accounts()
    found,_=retrieve_accounts("架空文具店\n取引日：2026/10/20\nNo. TEST\n専門書 4400円",accounts)
    assert [a["code"] for a in found]==["E107"]
    found,_=retrieve_accounts("架空機器販売\nNo. TEST\n業務用モニター 修理・部品交換",accounts)
    assert "E113" in [a["code"] for a in found]
    assert "A101" not in [a["code"] for a in found]


def test_mismatched_model_code_and_reason_is_held():
    value=candidate(account="E122",reason="専門書なのでE107（新聞図書費）の候補です")
    checked=ground(value,"文具店\n2026年10月20日\n合計 1,650円")
    assert checked["account"]==""
    assert any("コードと説明" in note for note in checked["warnings"])


def test_geometry_pairs_tax_label_with_its_amount_without_changing_text():
    lines=[{"text":"内消費税","box":[0,100,80,116]},{"text":"合計","box":[0,200,80,220]},
           {"text":"1,980円","box":[300,200,400,220]},{"text":"180円","box":[330,100,400,116]}]
    assert row_ordered_text(lines)=="内消費税 180円\n合計 1,980円"
    assert money_in("合計 1, 980円")==[1980]
