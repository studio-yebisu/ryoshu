// Actual UI/HTTP/encrypted persistence; saved fictional observations, no new OCR/AI score.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {spawn,execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash} from 'node:crypto';
const {chromium}=await import(process.env.RYOSHU_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.resolve(process.env.RYOSHU_FAVORITES_OUTPUT||path.join(root,'.local/account-favorites-qa',new Date().toISOString().replace(/[:.]/g,'-')));
const python=process.env.RYOSHU_QA_PYTHON||'python';
await fs.mkdir(out,{recursive:true});
const checks=[],errors=[],logs=[];
let server,serverExit,browser,page,address,expectedFavorites,shutdown;
const runFile=promisify(execFile);
async function api(route,body){return page.evaluate(async({route,body})=>{
  const response=await RyoshuSecurity.fetch('/api/'+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Ledger-Request':'1'},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw new Error(JSON.stringify(result));return result;
},{route,body});}
async function screenshot(name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});}
async function results(){await page.locator('.nav-item[data-view=results]').click();await page.locator('.results-table').waitFor();}
const row=id=>page.locator(`[data-result-row="${id}"]`);
const dialog=()=>page.locator('#account-change-dialog');
const quick=()=>page.locator('#account-change-form');
const detail=()=>page.locator('#review-form');
const choice=form=>form.locator('[data-account-choice]');
const category=form=>form.locator('[data-account-category]');
const star=(form,code)=>form.locator(`[data-favorite-toggle="${code}"]`);
const chip=(form,code)=>form.locator(`[data-account-favorites] [data-favorite-choice="${code}"]`);
async function open(id){await row(id).locator(`button.result-account[data-change-account="${id}"]`).click();await dialog().waitFor({state:'visible'});await quick().waitFor();}
async function close(){await page.keyboard.press('Escape');await dialog().waitFor({state:'hidden'});}
async function choose(form,account){await category(form).selectOption(account.category_id);await choice(form).selectOption(account.name);}
async function waitFavorite(code,value){await page.waitForFunction(({code,value})=>!state.favoriteSaving&&state.config.favorite_accounts.includes(code)===value,{code,value});}
async function toggle(form,code,value,key){const button=star(form,code);if(key){await button.focus();await page.keyboard.press(key);}else await button.click();await waitFavorite(code,value);assert.equal(await star(form,code).getAttribute('aria-pressed'),String(value));}
async function persistedRows(ids){return Promise.all(ids.map(id=>api('receipts/'+id)));}
async function assertLayout(locator){const layout=await locator.evaluate(node=>{const b=node.getBoundingClientRect();return{left:b.left,right:b.right,width:innerWidth,scroll:node.scrollWidth,client:node.clientWidth,documentScroll:document.documentElement.scrollWidth};});assert.ok(layout.left>=-1&&layout.right<=layout.width+1&&layout.scroll<=layout.client+1,JSON.stringify(layout));return layout;}
try{
  const launcherPath=path.join(root,'scripts/serve_browser_qa.py');
  const defaultData=path.join(process.env.USERPROFILE||process.env.HOME||'', 'StudioYebisu','ReceiptLedger','data');
  async function refused(args,message){
    try{await runFile(python,args,{cwd:root,windowsHide:true,timeout:30000});assert.fail('Unsafe QA configuration was accepted');}
    catch(error){assert.equal(error.code,2);assert.ok(error.stderr.includes(message),error.stderr);}
  }
  await refused([launcherPath,'--data-dir',defaultData],'通常の帳簿とは別');
  for(const field of ['DATA_DIR','PORT']){
    const destination=path.join(out,'guard-'+field.toLowerCase());
    const mismatch=`import os,runpy,sys\nfrom pathlib import Path\nos.environ['RECEIPT_LEDGER_DATA']=sys.argv[2]\nfrom receipt_ledger import config\nconfig.${field}=${field==='DATA_DIR'?"Path(sys.argv[2])/'wrong'":'-1'}\nsys.argv=[sys.argv[1],'--data-dir',sys.argv[2]]\nrunpy.run_path(sys.argv[0],run_name='__main__')`;
    await refused(['-c',mismatch,launcherPath,destination],'保存先・ポートとアプリの設定が一致しない');
    await assert.rejects(fs.access(destination));
  }
  checks.push('QA launcher refuses the default ledger path and cached data/port mismatches before creating a key or database');
  // Recreate only a historical custom name/code collision in an empty fictional DB.
  // The public add-account API correctly forbids creating this collision today.
  server=spawn(python,[launcherPath,'--data-dir',path.join(out,'data'),'--legacy-account-collision'],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  serverExit=new Promise(resolve=>server.once('exit',(code,signal)=>resolve({code,signal})));
  let pending='';
  address=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('QA server startup timed out')),30000);
    server.once('error',error=>{clearTimeout(timer);reject(error);});
    server.once('exit',code=>{clearTimeout(timer);reject(new Error('QA server exited:'+code));});
    server.stderr.on('data',chunk=>logs.push(chunk.toString()));
    server.stdout.on('data',chunk=>{pending+=chunk.toString();let i;while((i=pending.indexOf('\n'))>=0){const line=pending.slice(0,i);pending=pending.slice(i+1);try{const value=JSON.parse(line);if(value.qa_url){clearTimeout(timer);resolve(value);}}catch{logs.push(line+'\n');}}});
  });
  browser=await chromium.launch({headless:true,...(process.env.RYOSHU_BROWSER_CHANNEL?{channel:process.env.RYOSHU_BROWSER_CHANNEL}:{})});
  page=await browser.newPage({viewport:{width:1365,height:900}});
  page.on('pageerror',error=>errors.push(error.message));
  page.on('dialog',message=>message.type()==='beforeunload'?message.accept():message.dismiss());
  await page.goto(address.open_url,{waitUntil:'networkidle'});
  const health=await api('health');
  assert.equal(path.resolve(health.data_dir),path.join(out,'data'));
  assert.equal(path.resolve(health.source_dir),root);
  await page.locator('#file-input').setInputFiles(['V001','V044','V045'].map(id=>path.join(root,'samples/receipts-100-v2/individual',id+'.pdf')));
  await page.locator('#start-import').click();
  await page.waitForFunction(()=>state.records.length===3&&state.records.every(r=>!['queued','processing'].includes(r.status)),null,{timeout:30000});
  await results();
  const config=await api('config'),records=(await api('receipts')).receipts,ids=records.map(r=>r.id);
  const confirmedId=records.find(r=>r.original_name==='V001.pdf').id;
  const detailId=records.find(r=>r.original_name==='V044.pdf').id;
  const initial=await api('receipts/'+confirmedId);
  await api('receipts/'+confirmedId+'/save',{version:initial.version,draft:{...initial.draft,account:'消耗品費',purpose:'架空の業務用事務用品の確認',business_percent:100,payment:'bank',export_tax:'outside'},confirm:true});
  const baseline=await persistedRows(ids),history=await Promise.all(ids.map(id=>api('receipts/'+id+'/history')));
  assert.ok(baseline.find(r=>r.id===confirmedId).posting);
  const supplies=config.accounts.find(a=>a.name==='消耗品費'),it=config.accounts.find(a=>a.name==='通信費');
  const legacy=config.accounts.find(a=>a.name==='E129'),formal=config.accounts.find(a=>a.code==='E129');
  assert.ok(legacy&&formal&&legacy.code!==formal.code);
  assert.deepEqual(config.favorite_accounts,[]);
  await page.reload({waitUntil:'networkidle'});await open(confirmedId);
  await toggle(quick(),supplies.code,true,'Space');
  assert.equal(await star(quick(),supplies.code).evaluate(node=>node===document.activeElement),true);
  assert.equal(await chip(quick(),supplies.code).count(),1);
  assert.equal(await choice(quick()).inputValue(),supplies.name);
  assert.equal(await page.evaluate(id=>state.drafts.has(id),confirmedId),false);
  await toggle(quick(),supplies.code,false,'Enter');assert.equal(await chip(quick(),supplies.code).count(),0);
  await toggle(quick(),supplies.code,true);
  await choose(quick(),it);await toggle(quick(),it.code,true);
  await chip(quick(),supplies.code).click();assert.equal(await category(quick()).inputValue(),supplies.category_id);assert.equal(await choice(quick()).inputValue(),supplies.name);
  await chip(quick(),it.code).click();assert.equal(await category(quick()).inputValue(),it.category_id);assert.equal(await choice(quick()).inputValue(),it.name);
  assert.equal(await choice(quick()).evaluate(node=>node===document.activeElement),true);
  await category(quick()).selectOption('learning');assert.equal(await choice(quick()).inputValue(),'');
  const options=await choice(quick()).locator('option').evaluateAll(nodes=>nodes.map(node=>node.value).filter(Boolean));
  assert.ok(options.length&&options.every(name=>config.accounts.find(a=>a.name===name)?.category_id==='learning'));
  await choose(quick(),it);await screenshot('01-favorite-picker-desktop');await close();
  assert.deepEqual(await persistedRows(ids),baseline);
  checks.push('keyboard/mouse stars add and remove without receipt changes or dirty drafts; shortcuts synchronize category/account and the native two-layer selector remains intact');

  await page.reload({waitUntil:'networkidle'});await open(confirmedId);
  assert.equal(await chip(quick(),supplies.code).count(),1);assert.equal(await chip(quick(),it.code).count(),1);
  assert.equal(await star(quick(),supplies.code).getAttribute('aria-pressed'),'true');
  checks.push('favorites persist through a real browser reload');
  const failureRoute='**/api/accounts/'+supplies.code+'/favorite';
  await page.route(failureRoute,route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'架空の一時保存エラー'})}));
  await star(quick(),supplies.code).click();
  await page.waitForFunction(()=>!state.favoriteSaving&&document.querySelector('#toast')?.textContent.includes('保存できません'));
  assert.equal(await star(quick(),supplies.code).getAttribute('aria-pressed'),'true');
  assert.equal(await chip(quick(),supplies.code).count(),1);
  assert.ok((await api('config')).favorite_accounts.includes(supplies.code));
  await page.unroute(failureRoute);await toggle(quick(),supplies.code,false);await toggle(quick(),supplies.code,true);
  await close();assert.deepEqual(await persistedRows(ids),baseline);
  checks.push('a failed favorite POST shows an error, retains the existing star and shortcut, and succeeds on retry');

  await row(detailId).getByRole('button',{name:'詳細を修正',exact:true}).click();await detail().waitFor();
  assert.equal(await page.evaluate(id=>state.drafts.has(id),detailId),false);
  const detailAccount=config.accounts.find(a=>a.name===baseline.find(r=>r.id===detailId).draft.account);
  await toggle(detail(),detailAccount.code,false,'Enter');
  assert.equal(await star(detail(),detailAccount.code).evaluate(node=>node===document.activeElement),true);
  assert.equal(await page.evaluate(id=>state.drafts.has(id),detailId),false);
  // Hold a genuine HTTP response while the user moves focus elsewhere.
  let releaseResponse,routeEntered;
  const released=new Promise(resolve=>releaseResponse=resolve),entered=new Promise(resolve=>routeEntered=resolve);
  const delayedRoute='**/api/accounts/'+detailAccount.code+'/favorite';
  await page.route(delayedRoute,async route=>{routeEntered();await released;await route.continue();});
  await star(detail(),detailAccount.code).focus();await page.keyboard.press('Space');await entered;
  await detail().locator('[name=purpose]').focus();releaseResponse();await waitFavorite(detailAccount.code,true);
  assert.equal(await detail().locator('[name=purpose]').evaluate(node=>node===document.activeElement),true);
  await page.unroute(delayedRoute);
  assert.equal(await page.evaluate(id=>state.drafts.has(id),detailId),false);
  await detail().locator('[name=purpose]').fill('保存前の架空案件Aの調査費');await detail().locator('[name=total]').fill('12345');
  await chip(detail(),supplies.code).click();
  assert.equal(await category(detail()).inputValue(),supplies.category_id);assert.equal(await choice(detail()).inputValue(),supplies.name);
  assert.equal(await detail().locator('[name=purpose]').inputValue(),'保存前の架空案件Aの調査費');assert.equal(await detail().locator('[name=total]').inputValue(),'12345');
  await toggle(detail(),supplies.code,false);await toggle(detail(),supplies.code,true);
  assert.equal(await detail().locator('[name=purpose]').inputValue(),'保存前の架空案件Aの調査費');assert.equal(await detail().locator('[name=total]').inputValue(),'12345');
  assert.equal(await page.evaluate(id=>state.drafts.get(id)?.draft.account,detailId),supplies.name);
  await screenshot('02-favorites-detail-unsaved');
  assert.deepEqual(await persistedRows(ids),baseline);
  checks.push('detail shortcuts preserve unsaved amount/purpose, star-only actions never dirty a receipt, and delayed favorite responses do not steal focus');

  await page.reload({waitUntil:'networkidle'});await results();await open(confirmedId);
  await choose(quick(),legacy);assert.equal(await star(quick(),legacy.code).count(),1);
  await toggle(quick(),legacy.code,true);assert.ok(!(await api('config')).favorite_accounts.includes(formal.code));
  await choose(quick(),formal);assert.equal(await star(quick(),formal.code).count(),1);await toggle(quick(),formal.code,true);
  await chip(quick(),legacy.code).click();assert.equal(await choice(quick()).inputValue(),'E129');assert.equal(await category(quick()).inputValue(),legacy.category_id);assert.equal(await star(quick(),legacy.code).getAttribute('aria-pressed'),'true');
  await chip(quick(),formal.code).click();assert.equal(await choice(quick()).inputValue(),'事務用品費');assert.equal(await category(quick()).inputValue(),formal.category_id);
  await close();
  checks.push('historical custom name E129 and formal code E129 retain separate stable-code stars and correct category/account shortcuts');

  await page.locator('.more-nav summary').click();await page.locator('[data-view=catalog]').click();await page.locator('#catalog-rows').waitFor();
  const favoriteCodes=await page.locator('[data-catalog-favorite]').evaluateAll(nodes=>nodes.map(node=>node.dataset.favoriteToggle));
  assert.deepEqual([...favoriteCodes].sort(),config.accounts.map(a=>a.code).sort());
  assert.ok(!favoriteCodes.some(code=>['B101','B102','O101','O102','L101'].includes(code)));
  await page.locator('#catalog-category').selectOption('settlement');assert.equal(await page.locator('[data-catalog-favorite]').count(),0);
  await page.locator('#catalog-category').selectOption('');
  await toggle(page.locator('#catalog-rows'),formal.code,false,'Enter');
  assert.equal(await star(page.locator('#catalog-rows'),formal.code).evaluate(node=>node===document.activeElement),true);
  await results();await open(confirmedId);assert.equal(await chip(quick(),formal.code).count(),0);assert.equal(await chip(quick(),legacy.code).count(),1);await close();
  assert.deepEqual(await persistedRows(ids),baseline);
  checks.push('catalog exposes stars only for selectable expense/asset accounts and its changes synchronize with the picker');

  const long=await api('accounts',{name:'架空の長い名前でも折り返して選択できることを確かめるための制作素材利用料',kind:'expense',category_id:'other'});
  const expanded=await api('config');
  for(const account of expanded.accounts)await api('accounts/'+account.code+'/favorite',{favorite:true});
  expectedFavorites=expanded.accounts.map(a=>a.code).sort();
  await page.reload({waitUntil:'networkidle'});await results();await open(confirmedId);
  assert.equal(await quick().locator('[data-favorite-choice]').count(),expectedFavorites.length);
  await assertLayout(dialog());await screenshot('03-all-favorites-desktop');
  await page.setViewportSize({width:375,height:812});await assertLayout(dialog());
  const longChip=chip(quick(),long.code);await longChip.scrollIntoViewIfNeeded();await assertLayout(longChip);
  await longChip.click();assert.equal(await choice(quick()).inputValue(),long.name);assert.equal(await category(quick()).inputValue(),'other');
  await screenshot('04-all-favorites-mobile');await close();
  assert.deepEqual(await persistedRows(ids),baseline);
  assert.deepEqual(await Promise.all(ids.map(id=>api('receipts/'+id+'/history'))),history);
  checks.push('all favorites including a long custom name fit desktop and375px dialog widths; selecting a wrapped shortcut works without changing saved receipts, versions, statuses, postings or audit history');
  assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack||String(error));process.exitCode=1;if(page)await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});}
finally{
  if(page&&address)await page.evaluate(async token=>fetch('/_qa/stop',{method:'POST',headers:{'x-qa-token':token}}),address.stop_token).catch(()=>{});
  if(browser)await browser.close().catch(()=>{});
  if(server){let timer;shutdown=await Promise.race([serverExit,new Promise(resolve=>{timer=setTimeout(()=>resolve(null),10000);})]);clearTimeout(timer);if(!shutdown){server.kill();shutdown=await serverExit;}}
  if(expectedFavorites&&!errors.length){
    try{
      const program=`import json,sqlite3,sys\nfrom pathlib import Path\nfrom receipt_ledger.store import Store\ns=Store(Path(sys.argv[1]))\nassert s.ready\ntry:\n db=sqlite3.connect(s.path)\n db.execute('SELECT * FROM settings').fetchall()\n raise AssertionError('Database readable without a key')\nexcept sqlite3.DatabaseError: pass\nfinally: db.close()\nassert b'favorite_accounts' not in s.path.read_bytes()\nprint(json.dumps({'favorite_accounts':s.favorite_accounts(),'encrypted':True,'schema_version':s.catalog()['schema_version']}))`;
      const reopened=JSON.parse((await runFile(python,['-c',program,path.join(out,'data')],{cwd:root,windowsHide:true})).stdout);
      assert.deepEqual(reopened.favorite_accounts,expectedFavorites);assert.equal(reopened.schema_version,3);
      await fs.writeFile(path.join(out,'reopened-encrypted-store.json'),JSON.stringify(reopened,null,2)+'\n');
      checks.push('after stopping the QA server a separate Python process reopens the encrypted DB with all favorites and unchanged schema; unkeyed SQLite cannot read settings');
    }catch(error){errors.push(error.stack||String(error));process.exitCode=1;}
  }
  const sources={};for(const name of ['web/app.js','web/style.css','receipt_ledger/store.py','receipt_ledger/server.py','scripts/serve_browser_qa.py','scripts/account_favorites_smoke.mjs'])sources[name]=createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex');
  const result={status:errors.length?'failed':'passed',scope:'Actual browser/HTTP/encrypted settings using saved fictional observations and a reconstructed legacy custom-account collision. No new OCR/classification inference.',checks,errors,server_exit:shutdown,source_hashes:sources};
  await fs.writeFile(path.join(out,'verification.json'),JSON.stringify(result,null,2)+'\n');
  if(logs.length)await fs.writeFile(path.join(out,'server-private.log'),logs.join(''));
  console.log(JSON.stringify({status:result.status,checks:checks.length,output:out,errors}));
}
