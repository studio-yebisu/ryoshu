"""Real loopback HTTP checks on an isolated fictional ledger, never a user ledger."""
import argparse
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import socket
import sys
import threading
import time

import httpx
from PIL import Image
import uvicorn

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from receipt_ledger import config, __version__
from receipt_ledger.server import create_app


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists():raise SystemExit('Use a new isolated proof directory')
    args.out.mkdir(parents=True)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    config.PORT=port
    app=create_app(args.out/'data',run_workers=False)
    runner=uvicorn.Server(uvicorn.Config(app,access_log=False,log_level='error'))
    thread=threading.Thread(target=lambda:runner.run(sockets=[sock]),daemon=True)
    thread.start()
    checks=[]
    password='fictional HTTP security recovery password'
    def check(name,response,status):
        assert response.status_code==status,(name,response.status_code,response.text[:200])
        checks.append({'name':name,'expected_http':status,'observed_http':response.status_code,
                       'result':'拒否確認' if status>=400 else '受け入れ確認'})
    try:
        with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=30) as client:
            for _ in range(100):
                try:
                    if client.get('/api/health').status_code==200:break
                except httpx.TransportError:pass
                time.sleep(.05)
            health=client.get('/api/health')
            assert set(health.json())=={'app','app_version','instance'}
            check('未認証バックアップ取得',client.get('/api/backup'),401)
            check('未認証設定変更（旧Xヘッダーだけ）',client.post('/api/settings',headers={'X-Ledger-Request':'1'},json={'business_context':'fictional unauthorized'}),401)
            check('未認証CSV取得',client.get('/api/export/expenses'),401)
            bootstrap=app.state.sessions.mint_bootstrap()
            headers={'X-Ledger-Request':'1','Authorization':'Bootstrap '+bootstrap}
            response=client.post('/api/session',headers=headers);check('起動リンクの交換',response,200)
            client.headers.update({'Authorization':'Bearer '+response.json()['token'],'X-Ledger-Request':'1'})
            check('起動リンクの再利用',client.post('/api/session',headers=headers),401)
            check('保護設定前の帳簿取得',client.get('/api/receipts'),423)
            check('復元用パスワード設定',client.post('/api/security/open',json={'password':password,'confirmation':password}),200)
            check('異なるOriginからの設定変更',client.post('/api/settings',headers={'Origin':'https://invalid.example'},json={}),403)
            check('不正Host',client.get('/api/receipts',headers={'Host':'invalid.example'}),400)
            check('cross-site原本API',client.get('/api/receipts/fictional/original',headers={'Sec-Fetch-Site':'cross-site'}),403)
            raw=io.BytesIO();Image.new('RGB',(30,30),'white').save(raw,'PNG')
            response=client.post('/api/receipts',files={'files':('架空.png',raw.getvalue(),'image/png')});check('認証済み原本取込',response,200)
            receipt=response.json()['imported'][0]['id']
            store=app.state.store
            store.complete(receipt,{'text':'架空の検証店 合計1100円','page_count':1},None)
            row=client.get('/api/receipts/'+receipt).json()
            draft={**row['draft'],'date':'2026-10-22','merchant':'架空の検証店','total':1100,'account':'通信費','payment':'owner','business_percent':100,'purpose':'架空の業務用通信','export_tax':'outside'}
            check('本人確認による帳簿登録',client.post('/api/receipts/'+receipt+'/save',json={'version':row['version'],'draft':draft,'confirm':True}),200)
            exported=client.get('/api/export/expenses');check('外部AIなしのCSV出力',exported,200)
            assert b'1100' in exported.content
            check('原本を認証なしで取得',client.get('/api/receipts/'+receipt+'/original',headers={'Authorization':'Bearer invalid'}),401)
            original=client.get('/api/receipts/'+receipt+'/original');check('原本を認証して取得',original,200)
            assert original.content==raw.getvalue()
            bad=client.post('/api/backup',json={'password':'wrong fictional recovery password'});check('バックアップのパスワード違い',bad,422)
            check('パスワード試行の間隔制限',client.post('/api/backup',json={'password':password}),429)
            time.sleep(3.1)
            backup=client.post('/api/backup',json={'password':password});check('暗号化バックアップ作成',backup,200)
            assert backup.content.startswith(b'RYOSHU-BACKUP') and b'SQLite format 3' not in backup.content
            assert password not in bad.text
            result={'status':'passed','app_version':__version__,'data_kind':'fictional only','transport':'real TCP 127.0.0.1 ephemeral listener',
                    'public_health_fields':sorted(health.json()),'checks':checks,'csv_sha256':hashlib.sha256(exported.content).hexdigest(),
                    'database_header_is_plain_sqlite':store.path.read_bytes().startswith(b'SQLite format 3'),
                    'source_sha256':{str(path.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest() for path in (ROOT/'receipt_ledger').glob('*.py')}}
            assert not result['database_header_is_plain_sqlite']
    finally:
        runner.should_exit=True;thread.join(10);sock.close()
        if thread.is_alive():raise RuntimeError('Owned QA server did not stop')
    result['owned_listener_stopped']=True
    (args.out/'verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps({'status':result['status'],'checks':len(checks),'owned_listener_stopped':True}))


if __name__=='__main__':main()
