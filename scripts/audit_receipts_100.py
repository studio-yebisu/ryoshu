"""Exercise real HTTP upload/worker pipeline. Truth is read only by this scorer.

Each run uses a frozen app snapshot, independent database and append-only events.
No fixture text, expected values or per-case human context enters an LLM request.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import httpx

SOURCE=Path(__file__).resolve().parents[1]

def now():return datetime.now(timezone.utc).isoformat()
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def key(value):
    import re,unicodedata
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',value or '')).casefold()

def evaluate(truth,row,accounts):
    s=row.get('suggestion') or {}
    lookup={key(v):a['name'] for a in accounts for v in [a['name'],a['code'],*a.get('aliases',[])]}
    actual=lookup.get(key(s.get('account')),s.get('account',''))
    expected={lookup.get(key(v),v) for v in truth['acceptable_accounts']}
    blocked=bool(s.get('blocking_reasons'))
    if truth['expected_decision']=='block':decision=blocked and not actual
    elif truth['expected_decision']=='hold':decision=not actual and not blocked
    elif truth['expected_decision']=='asset_review':decision=not blocked and (s.get('account_kind')=='asset' or any(p.get('action')=='asset_review' for p in s.get('policy_matches',[])))
    else:decision=not blocked and actual in expected
    model_ok=bool(s.get('model')) and not row.get('error')
    handling=s.get('decision') or ('block' if blocked else 'hold' if not actual or any(s.get(k) in ('',None) for k in ['date','merchant','total']) else 'asset_review' if s.get('account_kind')=='asset' else 'propose')
    # Negative refunds cannot be represented as normal positive expense drafts.
    amount=truth['total']
    total_exact=s.get('total')==amount if amount is None or amount>=0 else None
    return {'ocr_completed':bool(row.get('ocr')),'model_completed':model_ok,'total_exact':total_exact,
            'date_exact':s.get('date','')==truth['date'],'merchant_exact':key(s.get('merchant'))==key(truth['merchant']),
            'account_or_decision_exact':bool(model_ok and decision),'blocked':blocked,
            'decision_exact':bool(model_ok and handling==truth['expected_decision']),
            'account_exact':bool(model_ok and actual in expected) if truth['expected_decision']=='propose' else None,
            'actual_decision':handling,
            'abstained':not actual,'error':row.get('error','')}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--fixtures',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--port',type=int,default=8767);ap.add_argument('--cases',default='');ap.add_argument('--reuse-ocr',type=Path);ap.add_argument('--source',type=Path,default=SOURCE);ap.add_argument('--resume',action='store_true');ap.add_argument('--keep-server',action='store_true')
    args=ap.parse_args();output=args.output.resolve()
    if (output/'run.json').exists() and not args.resume:raise SystemExit('Use a fresh run directory; previous evidence is immutable.')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1',args.port))==0:raise SystemExit(f'Port {args.port} already in use; no process will be replaced.')
    output.mkdir(parents=True,exist_ok=True);snapshot=output/'app'
    if not args.resume:
        snapshot.mkdir()
        for folder in ['receipt_ledger','data','web']:
            shutil.copytree(args.source/folder,snapshot/folder,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    source_hashes={str(p.relative_to(snapshot)).replace('\\','/'):sha(p) for p in snapshot.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    manifest=json.loads((args.fixtures/'manifest.json').read_text(encoding='utf-8'))
    selected=set(args.cases.split(',')) if args.cases else None
    cases=[c for c in manifest['cases'] if selected is None or c['case_id'] in selected]
    meta={'started_at':now(),'app_source':str(args.source),'snapshot':str(snapshot),'source_hashes':source_hashes,
          'fixture_manifest_sha256':sha(args.fixtures/'manifest.json'),'selected':[c['case_id'] for c in cases],
          'data_dir':str(output/'data'),'port':args.port,'status':'starting','pid':None,'ocr_reuse':str(args.reuse_ocr) if args.reuse_ocr else None,
          'model_input_policy':'PDF bytes and one global business description only; no truth, IDs or human context supplied to classification'}
    completed={}
    if args.resume:
        meta=json.loads((output/'run.json').read_text(encoding='utf-8'))
        assert meta['source_hashes']==source_hashes, 'Frozen source changed'
        assert meta['fixture_manifest_sha256']==sha(args.fixtures/'manifest.json'), 'Fixture changed'
        assert meta['selected']==[c['case_id'] for c in cases], 'Case selection changed'
        if (output/'observations.jsonl').exists():
            for line in (output/'observations.jsonl').read_text(encoding='utf-8').splitlines():
                item=json.loads(line);completed[item['case_id']]=item
        meta.setdefault('resumes',[]).append({'at':now(),'completed':len(completed)})
    (output/'run.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    env=dict(os.environ);env['RECEIPT_LEDGER_DATA']=str(output/'data');env['RECEIPT_LEDGER_PORT']=str(args.port);env['PYTHONPATH']=str(snapshot);env['RECEIPT_LEDGER_ENV_LABEL']='検証用・架空の領収書データ'
    if args.reuse_ocr:env['RECEIPT_LEDGER_OCR_REUSE']=str(args.reuse_ocr)
    log=(output/'server.log').open('ab')
    process=subprocess.Popen([sys.executable,'-m','receipt_ledger'],cwd=snapshot,env=env,stdout=log,stderr=subprocess.STDOUT)
    meta['pid']=process.pid
    (output/'run.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    base=f'http://127.0.0.1:{args.port}'
    try:
        with httpx.Client(base_url=base,timeout=180,trust_env=False,limits=httpx.Limits(max_keepalive_connections=0)) as client:
            def get(path):
                for attempt in range(8):
                    try:
                        result=client.get(path);result.raise_for_status();return result
                    except httpx.TransportError:
                        if process.poll() is not None or attempt==7:raise
                        time.sleep(min(1+attempt,5))
            for _ in range(90):
                if process.poll() is not None:raise RuntimeError('Owned app exited; inspect server.log')
                try:
                    response=client.get('/api/config');response.raise_for_status();break
                except httpx.ConnectError:time.sleep(1)
            else:raise RuntimeError('Owned app did not become ready')
            account_list=response.json()['accounts']
            response=client.post('/api/settings',headers={'X-Ledger-Request':'1'},json={'business_context':'個人事業主。ソフトウェア・ゲーム制作、デザイン業。科目は暫定候補で、実際の業務用途・事業割合・支払方法は本人が確認する。'});response.raise_for_status()
            mapping=meta.get('receipt_ids',{}) if args.resume else {}
            if args.resume:
                existing={r['id']:r for r in get('/api/receipts').json()['receipts']}
                for cid,rid in mapping.items():
                    if cid not in completed and existing[rid]['status']=='error':
                        response=client.post('/api/receipts/'+rid+'/retry',json={'version':existing[rid]['version']},headers={'X-Ledger-Request':'1'});response.raise_for_status()
            for start in ([] if args.resume else range(0,len(cases),50)):
                files=[]
                for case in cases[start:start+50]:
                    path=args.fixtures/case['file'];assert sha(path)==case['sha256']
                    files.append(('files',(path.name,path.read_bytes(),'application/pdf')))
                response=client.post('/api/receipts',files=files,headers={'X-Ledger-Request':'1'});response.raise_for_status();result=response.json()
                if result['errors']:raise RuntimeError(result['errors'])
                for item in result['imported']:mapping[item['name'].split('.')[0]]=item['id']
            meta['status']='processing';meta['receipt_ids']=mapping
            (output/'run.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
            started=time.monotonic()
            while len(completed)<len(cases):
                response=get('/api/receipts');rows={r['id']:r for r in response.json()['receipts']}
                for truth in cases:
                    cid=truth['case_id'];row=rows[mapping[cid]]
                    if cid in completed or row['status'] in ('queued','processing'):continue
                    response=get('/api/receipts/'+row['id']);row=response.json()
                    checks=evaluate(truth,row,account_list)
                    observation={'case_id':cid,'receipt_id':row['id'],'observed_at':now(),'expected':{k:truth[k] for k in ['merchant','date','total','acceptable_accounts','expected_decision']},'checks':checks,'record':row}
                    completed[cid]=observation
                    with (output/'observations.jsonl').open('a',encoding='utf-8') as handle:handle.write(json.dumps(observation,ensure_ascii=False)+'\n')
                    print(json.dumps({'case':cid,'total':row['suggestion'].get('total') if row['suggestion'] else None,'account':(row.get('suggestion') or {}).get('account'),'checks':checks},ensure_ascii=False),flush=True)
                summary={name:sum(x['checks'].get(name) is True for x in completed.values()) for name in ['ocr_completed','model_completed','total_exact','date_exact','merchant_exact','account_or_decision_exact','decision_exact','account_exact']}
                summary.update({'completed':len(completed),'total':len(cases),'elapsed_s':round(time.monotonic()-started,1),'active_stages':dict(Counter(r.get('stage','') for r in rows.values() if r['status']=='processing'))})
                report={**meta,'summary':summary,'cases':[completed[c['case_id']] for c in cases if c['case_id'] in completed]}
                (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                if len(completed)<len(cases):time.sleep(5)
                if time.monotonic()-started>4*3600:raise TimeoutError('Run exceeded four hours; results retained.')
            meta['status']='complete';meta['finished_at']=now();meta['summary']=summary
            (output/'run.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'complete':summary},ensure_ascii=False),flush=True)
    except Exception as exc:
        meta['status']='interrupted';meta['interruption']=type(exc).__name__+': '+str(exc)
        (output/'run.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
        raise
    finally:
        # Stop this owned app only; no global Ollama or normal ledger lifecycle changes.
        if process.poll() is None and not (args.keep_server and meta.get('status')=='complete'):
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            else:process.terminate()
            process.wait(timeout=20)
        log.close()

if __name__=='__main__':main()
