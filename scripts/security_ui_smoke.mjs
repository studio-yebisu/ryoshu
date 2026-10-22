// Isolated browser proof. OCR/model observations are replayed; protection and IO are real.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
const {chromium}=await import(process.env.RYOSHU_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.resolve(process.env.RYOSHU_SECURITY_UI_OUTPUT||path.join(root,'.local','security-ui-qa',new Date().toISOString().replace(/[:.]/g,'-')));
const python=process.env.RYOSHU_QA_PYTHON||'python';
await fs.mkdir(out,{recursive:true});
const password='fictional initial browser recovery password';
const replacement='fictional replaced browser recovery password';
const logs=[],errors=[],checks=[];
let base,stopToken,serverProcess,serverExit,browser,page;
let expectedReceipts=1;
const api=route=>page.evaluate(async route=>(await RyoshuSecurity.fetch('/api/'+route)).json(),route);
const post=(route,body)=>page.evaluate(async({route,body})=>{const response=await RyoshuSecurity.fetch('/api/'+route,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw new Error(JSON.stringify(result));return result;},{route,body});
async function setAllStatus(action){for(const row of (await api('receipts')).receipts)await post(`receipts/${row.id}/${action}`,{version:row.version});}
async function sourceHashes(){const hashes={};for(const name of ['web/app.js','web/import.js','web/security.js','web/index.html','web/style.css','receipt_ledger/server.py','scripts/serve_browser_qa.py','scripts/security_ui_smoke.mjs'])hashes[name]=createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex');return hashes;}
async function snapshot(name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:!(await page.locator('dialog[open]').count())});}
async function go(view){if(['settings','accounts','catalog'].includes(view)&&!await page.locator('.more-nav').evaluate(node=>node.open))await page.locator('.more-nav > summary').click();await page.locator(`[data-view="${view}"]`).first().click();}
async function startServer(){
  serverProcess=spawn(python,[path.join(root,'scripts/serve_browser_qa.py'),'--data-dir',path.join(out,'data'),'--first-run'],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  serverExit=new Promise(resolve=>serverProcess.once('exit',(code,signal)=>resolve({code,signal})));
  let partial='';
  const address=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('QA server did not start: '+logs.join('').slice(-2000))),30000);
    serverProcess.once('error',error=>{clearTimeout(timer);reject(error);});
    serverProcess.once('exit',code=>{clearTimeout(timer);reject(new Error('QA server exited before ready: '+code));});
    serverProcess.stderr.on('data',chunk=>logs.push(chunk.toString()));
    serverProcess.stdout.on('data',chunk=>{partial+=chunk.toString();let index;while((index=partial.indexOf('\n'))>=0){const line=partial.slice(0,index);partial=partial.slice(index+1);try{const value=JSON.parse(line);if(value.qa_url){clearTimeout(timer);resolve(value);}}catch{logs.push(line+'\n');}}});
  });
  base=address.qa_url;stopToken=address.stop_token;
  for(let i=0;i<100;i++){try{if((await fetch(base+'/api/health')).ok)return address;}catch{}await new Promise(resolve=>setTimeout(resolve,100));}
  throw new Error('QA server did not become ready');
}
async function stopServer(){
  if(base&&stopToken)await fetch(base+'/_qa/stop',{method:'POST',headers:{'x-qa-token':stopToken}}).catch(()=>{});
  if(serverExit){let timer;const result=await Promise.race([serverExit,new Promise(resolve=>{timer=setTimeout(()=>resolve(null),10000);})]);clearTimeout(timer);if(!result)serverProcess.kill();}
  await fs.writeFile(path.join(out,'server.log'),logs.join(''));
}
async function restore(archive,name,secret){
  const result=await new Promise((resolve,reject)=>{
    const child=spawn(python,[path.join(root,'scripts/restore_backup.py'),archive,path.join(out,name)],{cwd:root,windowsHide:true,stdio:['pipe','pipe','pipe']});
    let stdout='',stderr='';child.stdout.on('data',chunk=>stdout+=chunk.toString());child.stderr.on('data',chunk=>stderr+=chunk.toString());child.once('error',reject);child.once('exit',code=>resolve({code,stdout,stderr}));child.stdin.end(secret+'\n');
  });
  assert.equal(result.code,0,result.stderr);
  assert.equal(JSON.parse(result.stdout).receipts,expectedReceipts);
}
async function backupDialog(secret){await page.locator('[data-action="backup"]').click();const dialog=page.getByRole('dialog',{name:'暗号化バックアップを保存',exact:true});await dialog.locator('input[name="password"]').fill(secret);return dialog;}
try{
  const address=await startServer();
  browser=await chromium.launch({headless:true,...(process.env.RYOSHU_BROWSER_CHANNEL?{channel:process.env.RYOSHU_BROWSER_CHANNEL}:{})});
  page=await browser.newPage({viewport:{width:1365,height:1000},acceptDownloads:true});
  const loadedSourceHashes=await sourceHashes();
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(address.open_url,{waitUntil:'networkidle'});
  await page.locator('#protection-form').waitFor();
  assert.equal((await api('security')).mode,'new');
  assert.equal(await page.locator('#protection-form input[type="password"]').count(),2);
  assert.equal(await page.locator('#upload-top').isDisabled(),true);
  await snapshot('01-protection-first-run');
  await page.locator('#protection-form [name="password"]').fill(password);
  await page.locator('#protection-form [name="confirmation"]').fill(replacement);
  await page.locator('#protection-form input[type="checkbox"]').check();
  await page.locator('#protection-form button[type="submit"]').click();
  assert.match(await page.locator('#protection-error').innerText(),/一致しません/);
  assert.equal((await api('security')).mode,'new');
  await page.locator('#protection-form [name="confirmation"]').fill(password);
  await page.locator('#protection-form button[type="submit"]').click();
  await page.locator('#choose-files').waitFor();
  assert.equal((await api('security')).ready,true);
  assert.equal(await page.locator('#protection-form').count(),0);
  assert.equal(await page.evaluate(p=>Object.values(sessionStorage).some(v=>v.includes(p)),password),false);
  checks.push('actual first-run setup: masked confirmation, mismatch blocks setup, key creation and no password in browser storage');
  await snapshot('02-simple-home');
  await page.setViewportSize({width:375,height:900});await snapshot('02b-home-375px');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.setViewportSize({width:1365,height:1000});
  const manifest=JSON.parse(await fs.readFile(path.join(root,'samples/receipts-100-v2/manifest.json'),'utf8'));
  const sample=path.join(root,'samples/receipts-100-v2',manifest.cases.find(row=>row.case_id==='V001').file);
  await page.locator('#file-input').setInputFiles(sample);
  assert.equal((await api('receipts')).receipts.length,0);
  assert.equal(await page.locator('.pdf-options').evaluate(node=>node.open),false);
  assert.equal(await page.locator('#start-import').isDisabled(),false);
  await page.locator('#start-import').click();
  await page.waitForFunction(async()=>{const rows=(await(await RyoshuSecurity.fetch('/api/receipts')).json()).receipts;return rows.length===1&&rows.every(r=>r.status==='review');},{},{timeout:30000});
  await page.waitForFunction(()=>state.records.length===1&&state.records.every(r=>r.status==='review'),null,{timeout:10000});
  await page.locator('#home-result-link a').click();
  await page.locator('.results-table').waitFor();
  assert.deepEqual(await page.locator('.results-table th').allTextContents(),['原本','読み取った内容','勘定科目']);
  await page.waitForFunction(()=>document.querySelector('.result-image img')?.naturalWidth>0);
  await snapshot('03-three-column-results');
  const image=page.locator('.result-image');await image.click();
  await page.locator('#image-dialog[open]').waitFor();
  await page.waitForFunction(()=>document.querySelector('#image-dialog img')?.naturalWidth>0);
  await page.locator('#zoom-scale').selectOption('200');
  assert.equal(await page.locator('#image-dialog img').evaluate(node=>parseFloat(node.style.width)===node.naturalWidth*2),true);
  await snapshot('04-original-zoom');await page.keyboard.press('Escape');
  await page.waitForFunction(()=>!document.querySelector('#image-dialog').open&&!document.querySelector('#image-dialog').childElementCount&&!!document.activeElement?.dataset.zoomReceipt,null,{timeout:3000});
  await page.locator('.result-ocr summary').click();
  await page.waitForFunction(()=>!document.querySelector('.result-ocr pre').innerText.includes('開くと本文'));
  assert.ok((await page.locator('.result-ocr pre').innerText()).length>0);
  checks.push('PDF stays staged until Start; results show original/text/account, authenticated thumbnail, zoom200%, Escape focus return and OCR disclosure');
  const firstId=(await api('receipts')).receipts[0].id;
  // Hold one actual replay worker while polling/import refresh update other rows.
  // This exercises the same UI background path without running a model in UI QA.
  assert.equal((await fetch(base+'/_qa/replay-hold?held=true',{method:'POST',headers:{'x-qa-token':stopToken}})).status,200);
  await page.locator('#upload-top').click();
  const second=path.join(root,'samples/receipts-100-v2',manifest.cases.find(row=>row.case_id==='V002').file);
  await page.locator('#file-input').setInputFiles(second);await page.locator('#start-import').click();
  await page.waitForFunction(async()=>{const rows=(await(await RyoshuSecurity.fetch('/api/receipts')).json()).receipts;return rows.length===2&&rows.some(row=>row.status==='processing');},null,{timeout:10000});
  expectedReceipts=2;
  await go('results');
  const stableImage=page.locator(`[data-zoom-receipt="${firstId}"]`);
  await page.waitForFunction(id=>document.querySelector(`[data-zoom-receipt="${id}"] img`)?.naturalWidth>0,firstId);
  await stableImage.focus();
  await page.evaluate(id=>{window.__qaStableImage=document.querySelector(`[data-zoom-receipt="${id}"]`);},firstId);
  await page.evaluate(()=>poll());
  assert.equal(await stableImage.evaluate(node=>node===window.__qaStableImage&&document.activeElement===node),true);
  await stableImage.click();
  await page.locator('#image-dialog[open]').waitFor();
  await page.evaluate(()=>{window.__qaZoomImage=document.querySelector('#image-dialog img');});
  await page.evaluate(()=>refreshImported());
  assert.equal(await page.evaluate(()=>document.querySelector('#image-dialog').open&&document.querySelector('#image-dialog img')===window.__qaZoomImage&&window.__qaStableImage.isConnected),true);
  await page.keyboard.press('Escape');
  await page.waitForFunction(()=>!document.querySelector('#image-dialog').childElementCount&&document.activeElement===window.__qaStableImage,null,{timeout:3000});
  const firstRow=page.locator(`[data-result-row="${firstId}"]`);
  await firstRow.locator('.result-ocr summary').click();
  await page.waitForFunction(id=>!document.querySelector(`[data-result-row="${id}"] .result-ocr pre`).innerText.includes('開くと本文'),firstId);
  await firstRow.locator('.result-ocr pre').evaluate(node=>{window.__qaOcrNode=node;window.__qaOcrText=node.textContent;});
  await page.evaluate(async()=>{await poll();await refreshImported();});
  assert.equal(await page.evaluate(()=>window.__qaStableImage.isConnected&&window.__qaOcrNode.isConnected&&window.__qaOcrNode.textContent===window.__qaOcrText&&window.__qaOcrNode.closest('details').open),true);
  assert.equal((await fetch(base+'/_qa/replay-hold?held=false',{method:'POST',headers:{'x-qa-token':stopToken}})).status,200);
  await page.waitForFunction(async()=>{const rows=(await(await RyoshuSecurity.fetch('/api/receipts')).json()).receipts;return rows.length===2&&rows.every(row=>row.status==='review');},null,{timeout:10000});
  await page.evaluate(()=>poll());
  await page.waitForFunction(()=>state.records.length===2&&state.records.every(row=>row.status==='review'),null,{timeout:10000});
  assert.equal(await page.evaluate(()=>window.__qaStableImage.isConnected&&window.__qaOcrNode.isConnected&&window.__qaOcrNode.textContent===window.__qaOcrText),true);
  checks.push('held background job: poll and import refresh preserve unchanged image DOM/focus, open zoom, and expanded OCR body while another row updates');
  await firstRow.locator('.result-ocr summary').click();
  await stableImage.focus();
  const beforeUpdate=await api('receipts/'+firstId),updatedDescription='架空のバックグラウンド更新';
  await post('receipts/'+firstId+'/save',{version:beforeUpdate.version,draft:{...beforeUpdate.draft,description:updatedDescription},confirm:false,remember_account:false});
  await page.evaluate(()=>poll());
  assert.equal(await stableImage.evaluate(node=>node===window.__qaStableImage&&document.activeElement===node),true);
  assert.notEqual(await firstRow.locator('.result-description').innerText(),updatedDescription);
  await page.locator('.results-toolbar a').focus();
  await page.waitForFunction(({id,text})=>document.querySelector(`[data-result-row="${id}"] .result-description`)?.textContent===text,{id:firstId,text:updatedDescription});
  assert.equal(await page.evaluate(()=>state.records.every(row=>!busyRecord(row))),true);
  checks.push('after the last job, a focused stale row stays stable and refreshes from saved state on blur without another poll');
  await page.locator('#results-pending').check();
  await stableImage.click();await page.locator('#image-dialog[open]').waitFor();
  await setAllStatus('exclude');await page.evaluate(()=>poll());
  assert.equal(await page.evaluate(()=>document.querySelector('#image-dialog').open&&document.querySelectorAll('[data-result-row]').length===2),true);
  await page.keyboard.press('Escape');
  await page.waitForFunction(()=>!document.querySelector('#image-dialog').open&&!document.querySelector('#image-dialog').childElementCount&&!!document.activeElement?.dataset.zoomReceipt);
  await page.locator('.results-toolbar a').focus();
  await page.waitForFunction(()=>document.querySelectorAll('[data-result-row]').length===0);
  await setAllStatus('restore');await page.evaluate(async()=>{await loadReceipts();renderResults(document.querySelector('#main-content'),true);});
  await firstRow.locator('.result-ocr summary').click();
  await page.waitForFunction(id=>!document.querySelector(`[data-result-row="${id}"] .result-ocr pre`).innerText.includes('開くと本文'),firstId);
  const disclosed=await firstRow.locator('.result-ocr pre').innerText();
  await setAllStatus('exclude');await page.evaluate(()=>poll());
  assert.equal(await firstRow.locator('.result-ocr').evaluate(node=>node.open),true);
  assert.equal(await firstRow.locator('.result-ocr pre').innerText(),disclosed);
  await firstRow.locator('.result-ocr summary').click();await page.locator('.results-toolbar a').focus();
  await page.waitForFunction(()=>document.querySelectorAll('[data-result-row]').length===0);
  await setAllStatus('restore');await page.evaluate(async()=>{await loadReceipts();state.resultsOnlyPending=false;renderResults(document.querySelector('#main-content'),true);});
  checks.push('filter eviction, including an empty result set, defers replacing active zoom or expanded OCR until interaction is released');
  await page.locator(`[data-open-receipt="${firstId}"]`).click();
  await page.locator('#review-form [name="purpose"]').fill('架空の画面検証に使用');
  await page.locator('[data-action="save-draft"]').click();
  await page.waitForFunction(()=>!state.busy&&state.detail?.draft.purpose==='架空の画面検証に使用');
  await page.locator('[data-action="back-to-ledger"]').click();
  await page.locator('.results-toolbar [data-view="ledger"]').click();
  await page.waitForFunction(()=>!document.querySelector('#download-csv').disabled);
  const csvEvent=page.waitForEvent('download');await page.locator('#download-csv').click();
  const csv=await csvEvent;await csv.saveAs(path.join(out,'review.csv'));
  checks.push('correction persists and the result screen links directly to actual generic CSV download');
  await go('settings');
  await page.evaluate(()=>Object.defineProperty(window,'showSaveFilePicker',{value:undefined,writable:true,configurable:true}));
  let dialog=await backupDialog(password);
  assert.equal(await dialog.locator('input[name="password"]').getAttribute('type'),'password');
  assert.equal(await dialog.locator('details').evaluate(node=>node.open),false);
  const backupEvent=page.waitForEvent('download');await dialog.locator('[type="submit"]').click();
  const download=await backupEvent;const fallbackArchive=path.join(out,'fallback.ryoshubak');await download.saveAs(fallbackArchive);
  await dialog.waitFor({state:'detached'});
  await restore(fallbackArchive,'restored-before-reset',password);
  checks.push('fallback download produces a real encrypted backup that restores successfully');
  await page.waitForTimeout(3200);
  await page.locator('[data-action="change-password"]').click();
  dialog=page.getByRole('dialog',{name:'復元用パスワードを再設定',exact:true});
  await dialog.locator('[name="password"]').fill(replacement);
  await dialog.locator('[name="confirmation"]').fill(password);
  await dialog.locator('input[type="checkbox"]').check();
  await dialog.locator('[type="submit"]').click();assert.match(await dialog.locator('[role="alert"]').innerText(),/一致しません/);
  await dialog.locator('[name="confirmation"]').fill(replacement);
  page.once('dialog',native=>native.accept());await dialog.locator('[type="submit"]').click();
  await dialog.waitFor({state:'detached'});
  await page.waitForTimeout(3200);
  dialog=await backupDialog(password);await dialog.locator('[type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('.backup-dialog [role="alert"]')?.innerText.includes('違う'));
  await dialog.locator('[data-cancel]').click();
  await restore(fallbackArchive,'old-backup-after-reset',password);
  checks.push('password reset requires confirmation; old password rejected for new backup while existing backup still restores with old password');
  await page.waitForTimeout(3200);
  // Browser-native OPFS FileSystemFileHandle exercises the actual streaming path.
  // Only the OS file picker is replaced; no fake WritableStream or backup response.
  await page.evaluate(()=>{window.__qaSaveCalls=[];window.showSaveFilePicker=async options=>{window.__qaSaveCalls.push(options.suggestedName);const folder=await navigator.storage.getDirectory();return folder.getFileHandle('streamed.ryoshubak',{create:true});};});
  dialog=await backupDialog(replacement);await dialog.locator('[type="submit"]').click();
  await dialog.waitFor({state:'detached'});
  const bytes=await page.evaluate(async()=>{const folder=await navigator.storage.getDirectory();const handle=await folder.getFileHandle('streamed.ryoshubak');return Array.from(new Uint8Array(await(await handle.getFile()).arrayBuffer()));});
  const streamedArchive=path.join(out,'streamed.ryoshubak');await fs.writeFile(streamedArchive,Buffer.from(bytes));
  assert.equal(await page.evaluate(()=>window.__qaSaveCalls.length),1);
  await restore(streamedArchive,'restored-after-reset',replacement);
  for(const archive of [fallbackArchive,streamedArchive])assert.equal((await fs.readFile(archive)).subarray(0,14).toString('latin1'),'RYOSHU-BACKUP\x01');
  assert.notEqual((await fs.readFile(path.join(out,'data/ledger.enc.sqlite3'))).subarray(0,16).toString(),'SQLite format 3\0');
  checks.push('File System Access streaming uses native OPFS writable and yields a restorable encrypted backup; OS save dialog itself is outside this automated proof');
  await go('results');await page.setViewportSize({width:375,height:900});
  await page.locator('.result-image').first().scrollIntoViewIfNeeded();
  await page.waitForFunction(()=>document.querySelector('.result-image img')?.naturalWidth>0);
  await snapshot('05-results-375px');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  checks.push('375px results keep horizontal scrolling within the result table');
  assert.deepEqual(errors,[]);
  assert.deepEqual(await sourceHashes(),loadedSourceHashes,'UI source changed during browser run; rerun against a stable candidate');
  const result={status:'passed',scope:'Real browser setup, Windows DPAPI/SQLCipher, original and CSV IO, password reset, encrypted backup/restore. OCR/model saved-observation replay. Native OS save dialog replaced with real browser OPFS FileSystemFileHandle.',checks,errors,receipt_count:expectedReceipts,source_sha256:loadedSourceHashes};
  await fs.writeFile(path.join(out,'record.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({status:result.status,checks:checks.length,out}));
}catch(error){if(page){await snapshot('FAILED').catch(()=>{});await fs.writeFile(path.join(out,'failure-ui.json'),JSON.stringify(await page.evaluate(()=>({view:typeof state==='undefined'?null:state.view,dialogOpen:document.querySelector('#image-dialog')?.open,activeTag:document.activeElement?.tagName,activeId:document.activeElement?.id,activeDataset:{...document.activeElement?.dataset}})).catch(()=>null),null,2));}await fs.writeFile(path.join(out,'failure.txt'),error.stack);throw error;}
finally{await browser?.close();await stopServer();}
