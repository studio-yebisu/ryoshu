"""Export integration proof using explicitly reviewed fictional records.

This is separate from the OCR/classification score. Oracle fields are used only
to populate a human-review scenario, never for a model request.
"""
import argparse,csv,hashlib,io,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from receipt_ledger.server import create_app
from receipt_ledger.schemas import Draft

ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--fields',type=Path)
args=ap.parse_args();source=Path(__file__).resolve().parents[1]
if args.out.exists():raise SystemExit('Use a fresh proof directory')
args.out.mkdir(parents=True)
fixtures=source/'samples/receipts-100-v2';manifest=json.loads((fixtures/'manifest.json').read_text(encoding='utf8'));cases={c['case_id']:c for c in manifest['cases']}
def reviewed_ocr(cid):
    if args.fields:
        return json.loads((args.fields/cid/'ocr.json').read_text(encoding='utf8'))
    return {'text':'Fictional manually reviewed CSV scenario; no OCR or model inference.', 'pages':[], 'verification_only':True}
scenario=[('V001','消耗品費','owner',100),('V021','旅費交通費','bank',100),('V005','消耗品費','cash',100),('V042','水道光熱費','cash',50),('V061','印刷製本費','bank',100),('V004','工具器具備品','owner',100)]
from receipt_ledger.protection import LedgerKeys
keys=LedgerKeys(args.out/'data');keys.setup('fictional CSV verification recovery password')
app=create_app(args.out/'data',run_workers=False);headers={'X-Ledger-Request':'1'};proof=[]
with TestClient(app) as client:
    bootstrap=app.state.sessions.mint_bootstrap()
    result=client.post('/api/session',headers={**headers,'Authorization':'Bootstrap '+bootstrap});result.raise_for_status()
    client.headers['Authorization']='Bearer '+result.json()['token']
    for cid,account,payment,percent in scenario:
        c=cases[cid];path=fixtures/c['file'];original=path.read_bytes()
        assert hashlib.sha256(original).hexdigest()==c['sha256']
        upload=client.post('/api/receipts',files={'files':(path.name,original,'application/pdf')},headers=headers);upload.raise_for_status()
        rid=upload.json()['imported'][0]['id'];ocr=reviewed_ocr(cid)
        app.state.store.complete(rid,ocr,None)
        d=Draft(date=c['date'],merchant=c['merchant'],total=c['total'],account=account,payment=payment,business_percent=percent,
                description=c['title'],purpose='CSV動作確認用の架空取引。'+('在宅事務所の使用分50%を想定。' if percent==50 else '')+c['human_context'],export_tax='outside')
        row=client.get('/api/receipts/'+rid).json();saved=client.post('/api/receipts/'+rid+'/save',json={'version':row['version'],'draft':d.model_dump(),'confirm':True},headers=headers);saved.raise_for_status()
        proof.append({'case_id':cid,'receipt_id':rid,'business':saved.json()['business_amount'],'private':saved.json()['private_amount']})
    c=cases['V038'];path=fixtures/c['file']
    pending=client.post('/api/receipts',files={'files':(path.name,path.read_bytes(),'application/pdf')},headers=headers).json()['imported'][0]['id']
    app.state.store.complete(pending,reviewed_ocr('V038'),None)
    expected_gross=sum(cases[cid]['total'] for cid,_,_,_ in scenario)
    results=[]
    for profile in ['generic','freee','mf','yayoi']:
        q=f'profile={profile}&kind=journal&tax_mode=reviewed'
        preview=client.get('/api/export-preview?'+q);preview.raise_for_status();p=preview.json()
        assert p['ready'] and p['receipt_count']==6
        response=client.get('/api/export/journal?'+q);response.raise_for_status()
        filename=f'qa-exempt-{profile}-journal.csv';(args.out/filename).write_bytes(response.content)
        encoding='cp932' if profile=='yayoi' else 'utf-8-sig';rows=list(csv.reader(io.StringIO(response.content.decode(encoding))))
        data=rows if profile=='yayoi' else rows[1:]
        assert len(data)==7
        if profile=='generic':debit=sum(int(r[2]) for r in data);credit=sum(int(r[4]) for r in data)
        else:
            di,ci=(7,13) if profile=='freee' else (8,16) if profile=='mf' else (8,14)
            debit=sum(int(r[di] or 0) for r in data);credit=sum(int(r[ci] or 0) for r in data)
        assert debit==credit==expected_gross
        results.append({'profile':profile,'file':filename,'receipts':6,'rows':len(data),'debit':debit,'credit':credit,'encoding':encoding,'sha256':hashlib.sha256(response.content).hexdigest()})
    generic=client.get('/api/export/expenses');generic.raise_for_status();(args.out/'qa-exempt-generic-expenses.csv').write_bytes(generic.content)
    result={'scenario':'架空の免税個人事業主。6件を原本の期待値で手動確認した出力検証。OCR・AIの正答率とは別集計。','records':proof,'expected_gross':expected_gross,'exports':results,'unit':'JPY','source':'actual /api/receipts, /save, /export-preview, /export endpoints; test client; workers disabled'}
    (args.out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False),flush=True)
