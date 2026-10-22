import csv
import io
import pytest
from support import LedgerClient as TestClient
from receipt_ledger.exports import build_export, tax_labels, transition_percent
from support import ready_app as create_app
from receipt_ledger.schemas import Draft
from support import ready_store as Store


def posted(store, **changes):
    row,_=store.add('fictional-test.pdf','.pdf',b'unique-'+str(changes).encode())
    store.complete(row['id'],{'text':'領収書 合計2,001円','page_count':1,'pages':[]},None)
    row=store.get(row['id'])
    values=dict(date='2026-10-02',merchant='架空商店,新店',total=2001,description='コピー用紙',purpose='業務資料\n原本確認済',account='消耗品費',business_percent=50,payment='bank',tax_rate='10',export_tax='purchase10')
    values.update(changes)
    return store.save(row['id'],row['version'],Draft(**values),confirm=True)


@pytest.mark.parametrize('profile,di,ci,ti,ai',[('freee',7,13,1,4),('mf',8,16,1,2),('yayoi',8,14,3,4)])
def test_native_compound_journal_preserves_gross_private_split_and_quotes(tmp_path,profile,di,ci,ti,ai):
    store=Store(tmp_path);r=posted(store)
    unconfirmed,_=store.add('pending.pdf','.pdf',b'pending')
    out=build_export(store.list(),profile)
    assert not out.issues and out.receipt_count==1 and out.debit_total==out.credit_total==2001
    rows=list(csv.reader(io.StringIO(out.content().decode(out.encoding))))
    data=rows if profile=='yayoi' else rows[1:]
    assert [int(v[di]) for v in data]==[1001,1000]
    assert sum(int(v[ci] or 0) for v in data)==2001
    assert {v[ti] for v in data}=={'2026/10/02'}
    assert [v[ai] for v in data]==['消耗品費','事業主貸']
    if profile=='yayoi':
        assert len(data[0])==25 and [v[0] for v in data]==['2110','2101']
        assert r['id'] in data[0][21]
    if profile=='mf':
        assert len(rows[0])==27 and rows[0][7]=='借方インボイス'
        assert data[0][7]=='適格' and data[1][6]=='対象外'
    if profile=='freee':assert rows[0][0]=='[表題行]' and data[0][0]=='[明細行]'


def test_personal_payment_exports_business_portion_once(tmp_path):
    store=Store(tmp_path);posted(store,payment='owner')
    out=build_export(store.list(),'mf')
    assert len(out.rows)==1 and out.debit_total==out.credit_total==1001
    assert out.rows[0][10]=='事業主借'


def test_tax_is_explicit_and_missing_values_block_whole_native_download(tmp_path):
    store=Store(tmp_path);posted(store,export_tax='')
    for target in ['freee','mf','yayoi']:
        out=build_export(store.list(),target)
        assert out.issues and not out.preview()['ready']
        with pytest.raises(ValueError,match='未確認'):out.content()
        exempt=build_export(store.list(),target,tax_mode='exempt')
        assert not exempt.issues and exempt.receipt_count==1
    assert build_export(store.list(),'generic',kind='expenses').content()


@pytest.mark.parametrize('day,pct',[('2026-09-30',80),('2026-10-01',70),('2028-09-30',70),('2028-10-01',50),('2030-10-01',30)])
def test_confirmed_transition_treatment_uses_transaction_date(day,pct):
    assert transition_percent(day)==pct
    labels=tax_labels('transition8',day)
    assert labels['freee']==f'課対仕入（控{pct}）8%（軽）'
    assert labels['invoice']==f'{pct}％控除'
    assert labels['yayoi'].endswith(f'区分{pct}%')


def test_mixed_rate_and_unrepresentable_text_are_reported_without_partial_export(tmp_path):
    store=Store(tmp_path);posted(store,tax_rate='mixed')
    assert '混在' in build_export(store.list(),'mf').issues[0]['message']
    store=Store(tmp_path/'unicode');posted(store,merchant='架空店😀')
    assert 'Shift-JIS' in build_export(store.list(),'yayoi').issues[0]['message']
    assert not build_export(store.list(),'freee').issues


def test_review_csv_retains_unknown_amounts_status_and_formula_safety(tmp_path):
    store=Store(tmp_path);r,_=store.add('=1+1.pdf','.pdf',b'pending')
    out=build_export(store.list(),'generic',kind='review')
    rows=list(csv.reader(io.StringIO(out.content().decode('utf-8-sig'))))
    assert rows[1][0]=='順番待ち' and rows[1][3]=='' and rows[1][9]=="'=1+1.pdf"
    assert not build_export(store.list(),'generic',kind='journal').rows


def test_yayoi_unit_compatibility_keeps_original_notes(tmp_path):
    store=Store(tmp_path);row=posted(store,description='ガス18m³・①・¥2,001')
    out=build_export(store.list(),'yayoi')
    assert out.preview()['ready'] and any('標準表記' in n for n in out.warnings)
    assert '18m3' in out.content().decode('cp932')
    assert store.get(row['id'])['draft']['description']=='ガス18m³・①・¥2,001'


def test_preview_download_and_account_mapping_use_same_saved_postings(tmp_path):
    app=create_app(tmp_path,run_workers=False);posted(app.state.store)
    with TestClient(app) as client:
        p=client.get('/api/export-preview?profile=mf&kind=journal').json()
        assert p['ready'] and p['debit_total']==2001
        headers={'X-Ledger-Request':'1'}
        result=client.post('/api/export-map',json={'profile':'mf','names':{'消耗品費':'事務用品費'}},headers=headers)
        assert result.status_code==200
        assert Store(tmp_path).export_map('mf')=={'消耗品費':'事務用品費'}
        r=client.get('/api/export/journal?profile=mf')
        assert r.status_code==200 and '事務用品費' in r.content.decode('utf-8-sig')
        assert client.get('/api/export/journal?profile=unrecognized').status_code==422
