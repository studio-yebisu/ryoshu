// Optional developer check. Uses an isolated database and saved observations, not OCR/AI.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
const {chromium}=await import(process.env.RYOSHU_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.resolve(process.env.RYOSHU_BROWSER_OUTPUT||path.join(root,'.local','browser-qa',new Date().toISOString().replace(/[:.]/g,'-')));
await fs.mkdir(out,{recursive:true});
let base,stopToken,serverProcess,serverExit;
const log=[];
async function startServer(){
  serverProcess=spawn(process.env.RYOSHU_QA_PYTHON||'python',[path.join(root,'scripts','serve_browser_qa.py'),'--data-dir',path.join(out,'data')],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  serverExit=new Promise(resolve=>serverProcess.once('exit',(code,signal)=>resolve({code,signal})));
  let partial='';
  const address=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('QA server did not start: '+log.join('').slice(-2000))),30000);
    serverProcess.once('error',error=>{clearTimeout(timer);reject(error);});
    serverProcess.once('exit',code=>{clearTimeout(timer);reject(new Error('QA server exited before ready: '+code+' '+log.join('').slice(-2000)));});
    serverProcess.stderr.on('data',chunk=>log.push(chunk.toString()));
    serverProcess.stdout.on('data',chunk=>{
      partial+=chunk.toString();let index;
      while((index=partial.indexOf('\n'))>=0){
        const line=partial.slice(0,index);partial=partial.slice(index+1);
        try{const value=JSON.parse(line);if(value.qa_url){clearTimeout(timer);resolve({url:value.qa_url,token:value.stop_token});}}catch{log.push(line+'\n');}
      }
    });
  });
  base=address.url;stopToken=address.token;
  for(let i=0;i<100;i++){try{const response=await fetch(address.url+'/api/config');if(response.ok)return address;}catch{}await new Promise(resolve=>setTimeout(resolve,100));}
  throw new Error('QA server did not become ready');
}
async function stopServer(){
  if(base&&stopToken)await fetch(base+'/_qa/stop',{method:'POST',headers:{'x-qa-token':stopToken}}).catch(()=>{});
  if(serverExit){let timer;const result=await Promise.race([serverExit,new Promise(resolve=>{timer=setTimeout(()=>resolve(null),10000);})]);clearTimeout(timer);if(!result)serverProcess.kill();}
  await fs.writeFile(path.join(out,'server.log'),log.join(''));
}
async function sourceHashes(){
  const result={};
  for(const name of ['web/app.js','web/import.js','web/index.html','web/style.css','receipt_ledger/server.py','scripts/serve_browser_qa.py','scripts/browser_smoke.mjs'])result[name]=createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex');
  return result;
}
const manifest=JSON.parse(await fs.readFile(root+'/samples/receipts-100-v2/manifest.json','utf8'));
let browser, page;
const errors=[],checks=[];
const api=async(route)=> (await page.request.get(base+'/api/'+route)).json();
const snapshot=async(name)=>page.screenshot({path:path.join(out,name+'.png'),fullPage:!(await page.locator('dialog[open]').count())});
async function waitImported(n){await page.waitForFunction(n=>document.querySelector('#import-progress').innerText.includes(`${n} / ${n}ファイル`),n,{timeout:120000});}
async function openResults(){if(!await page.locator('#import-progress details').evaluate(node=>node.open))await page.locator('#import-progress summary').click();await page.locator('.import-result-controls').waitFor();}
async function waitComplete(n){await page.waitForFunction(async n=>{const d=await(await fetch('/api/receipts')).json();return d.receipts.length===n&&d.receipts.every(r=>!['queued','processing'].includes(r.status));},n,{timeout:120000});await page.waitForFunction(n=>state.records.length===n&&state.records.every(r=>!['queued','processing'].includes(r.status)),n,{timeout:15000});}
try{
  const launched=await startServer();base=launched.url;stopToken=launched.token;
  browser=await chromium.launch({headless:true,...(process.env.RYOSHU_BROWSER_CHANNEL?{channel:process.env.RYOSHU_BROWSER_CHANNEL}:{})});
  page=await browser.newPage({viewport:{width:1365,height:1000}});page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/',{waitUntil:'networkidle'});
  await page.locator('.dropzone').waitFor();assert.equal(await page.locator('#page-title').innerText(),'経費帳');
  assert.match(await page.locator('#usage-guidance').innerText(),/必ずご自身で確認/);
  assert.match(await page.locator('#usage-guidance').innerText(),/税理士に確認/);
  const help=await page.request.get(base+'/help.html');assert.equal(help.status(),200);
  assert.match(await help.text(),/税理士の承認や税法への適合を表すものではありません/);
  checks.push('personal and tax-professional review policy visible in app and local help');
  await snapshot('01-empty-ledger');
  await page.locator('#upload-top').click();assert.equal(await page.locator('[name="pdf-mode"]:checked').count(),0);
  await page.locator('#file-input').setInputFiles(manifest.cases.map(c=>root+'/samples/receipts-100-v2/'+c.file));
  assert.match(await page.locator('#import-selection').innerText(),/100ファイル/);
  assert.equal(await page.locator('#start-import').isDisabled(),true);
  await snapshot('02-pdf-selection');
  await page.locator('[name="pdf-mode"][value="document"]').check();await page.locator('#start-import').click();
  const pauseButton=await page.locator('[data-import-action="pause"]').elementHandle();await page.waitForFunction(async()=>(await(await fetch('/api/receipts')).json()).receipts.length>=3);assert.equal(await pauseButton.evaluate(node=>node===document.querySelector('[data-import-action="pause"]')),true);
  await page.locator('[data-import-action="pause"]').click();await page.waitForTimeout(400);
  const paused=(await api('receipts')).receipts.length;await page.waitForTimeout(400);assert.equal((await api('receipts')).receipts.length,paused);
  await snapshot('03-import-paused');await page.locator('[data-import-action="resume"]').click();
  await waitImported(100);await waitComplete(100);await page.reload({waitUntil:'networkidle'});
  checks.push('100 PDFs selected once, explicit PDF mode, pause/resume, saved and read to ledger');
  await page.locator('#export-profile').selectOption('freee');await page.waitForFunction(()=>document.querySelector('#export-result').innerText.includes('確認済み'));
  assert.equal(await page.locator('#download-csv').isDisabled(),true);checks.push('accounting CSV explains why unreviewed rows cannot be exported');
  await page.locator('#export-profile').selectOption('generic');
  await page.locator('#ledger-search').fill('V001.pdf');await page.waitForFunction(()=>document.querySelectorAll('#ledger-table tbody tr').length===1);await page.locator('#ledger-search').fill('');await page.waitForFunction(()=>document.querySelectorAll('#ledger-table tbody tr').length===100);
  checks.push('ledger search');
  let rows=(await api('receipts')).receipts;
  for(const name of ['V001.pdf','V038.pdf']){const row=rows.find(r=>r.original_name===name);assert.ok(row);await page.locator(`[data-ledger-select="${row.id}"]`).check();}
  await page.locator('[data-action="review-ledger"]').click();
  assert.match(await page.locator('#batch-missing-warning').innerText(),/2件に入力の不足/);
  assert.match(await page.locator('#review-dialog').innerText(),/不足：.*業務での用途/);
  assert.equal(await page.locator('#confirm-batch').isDisabled(),true);
  await page.locator('#review-dialog .dialog-heading button').click();
  assert.equal((await api('receipts')).receipts.filter(r=>r.status==='confirmed').length,0);
  checks.push('batch review warns about missing inputs before any confirmation request');
  await page.locator('#batch-purpose').fill('制作業務のために使用');await page.locator('#batch-percent').fill('100');await page.locator('#batch-payment').selectOption('owner');await page.locator('#batch-tax').selectOption('outside');
  await page.locator('[data-action="review-ledger"]').click();assert.equal(await page.locator('#confirm-batch').isDisabled(),true);await snapshot('04-batch-review');
  await page.locator('#review-ack').check();await page.locator('#confirm-batch').click();
  await page.waitForFunction(()=>document.querySelector('#batch-result').innerText.includes('1件を確認済みにしました'));
  rows=(await api('receipts')).receipts;assert.equal(rows.filter(r=>r.status==='confirmed').length,1);assert.equal(rows.find(r=>r.original_name==='V038.pdf').draft.total,null);
  checks.push('explicit batch review acknowledgement, missing amount stays pending, only valid row posted');

  assert.match(await page.locator('#batch-result').innerText(),/V038.pdf/);
  await page.locator('#batch-result [data-open-receipt]').click();
  await page.locator('[data-action="back-to-ledger"]').click();
  assert.match(await page.locator('#batch-result').innerText(),/V038.pdf/);
  checks.push('receipt detail returns to the ledger and retains batch results');
  await page.locator('[data-action="filter-batch-issues"]').click();assert.equal(await page.locator('#ledger-rows tr').count(),1);
  await page.locator('[data-action="clear-issue-filter"]').click();assert.equal(await page.locator('#ledger-rows tr').count(),100);
  checks.push('named batch failures persist after opening a receipt and can filter the remaining rows');

  await page.locator('#export-profile').selectOption('freee');await page.waitForFunction(()=>!document.querySelector('#download-csv').disabled);
  const download=page.waitForEvent('download');await page.locator('#download-csv').click();const csv=await download;await csv.saveAs(path.join(out,'ui-freee.csv'));
  await page.locator('#export-new-only').check();await page.waitForFunction(()=>document.querySelector('#download-csv').disabled);assert.match(await page.locator('#export-result').innerText(),/前回CSV作成/);
  checks.push('freee CSV download and version-aware CSV creation history');
  await page.locator('#export-new-only').uncheck();await page.locator('#export-profile').selectOption('generic');

  await page.waitForFunction(()=>!document.querySelector('#download-csv').disabled);
  await page.route('**/api/export-preview?*',route=>route.abort());
  await page.locator('#export-kind').selectOption('journal');
  await page.waitForFunction(()=>document.querySelector('#export-result').innerText.includes('アプリに接続できません'));
  assert.equal(await page.locator('#download-csv').isDisabled(),true);
  await page.unroute('**/api/export-preview?*');await page.locator('#export-kind').selectOption('review');await page.waitForFunction(()=>!document.querySelector('#download-csv').disabled);
  checks.push('CSV preview network failure disables save and explains recovery in Japanese');

  await page.locator('#upload-top').click();await page.locator('#file-input').setInputFiles([{name:'壊れた.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-invalid')},{name:'V001.pdf',mimeType:'application/pdf',buffer:await fs.readFile(root+'/samples/receipts-100-v2/'+manifest.cases[0].file)}]);await page.locator('[name="pdf-mode"][value="document"]').check();await page.locator('#start-import').click();await waitImported(2);
  await openResults();await page.waitForFunction(()=>document.querySelector('#import-progress').innerText.includes('PDFを開けません'));assert.match(await page.locator('#import-progress').innerText(),/重複1件/);assert.match(await page.locator('#import-progress').innerText(),/PDFを開けません/);
  checks.push('persistent individual import errors and exact duplicate skip');
  await page.locator('[data-import-action="dismiss"]').click();
  await page.locator('#upload-top').click();await page.locator('#file-input').setInputFiles(root+'/samples/receipts-100-v2/receipts-100-review.pdf');await page.locator('[name="pdf-mode"][value="document"]').check();await page.locator('#start-import').click();await waitImported(1);await page.locator('[data-import-action="split-failed"]').waitFor();assert.equal(await page.locator('#import-progress details').evaluate(node=>node.open),false);await snapshot('04b-pdf-mode-remedy');await page.locator('[data-import-action="split-failed"]').click();await waitImported(1);await waitComplete(200);await page.reload({waitUntil:'networkidle'});
  assert.equal(await page.locator('#ledger-table tbody tr').count(),100);assert.match(await page.locator('.pagination').innerText(),/1 \/ 2ページ/);
  await page.locator('[data-action="ledger-next"]').click();assert.match(await page.locator('.pagination').innerText(),/2 \/ 2ページ/);
  rows=(await api('receipts')).receipts;const split=rows.find(r=>r.source_document?.page===100);assert.ok(split);assert.equal(split.source_document.page_count,100);
  const source=await page.request.get(`${base}/api/receipts/${split.id}/source`);assert.equal((await source.body()).compare(await fs.readFile(root+'/samples/receipts-100-v2/receipts-100-review.pdf')),0);
  checks.push('100-page PDF split, immutable source download, 100-row pagination');
  await snapshot('05-ledger-200');

  const duplicate=rows.find(r=>r.possible_duplicate_ids?.length);assert.ok(duplicate);
  await page.locator('#ledger-search').fill(duplicate.original_name);
  await page.locator(`[data-open-receipt="${duplicate.id}"]`).click();
  assert.match(await page.locator('#detail-duplicates').innerText(),/別の支出か、二重取り込みか/);
  const related=duplicate.possible_duplicate_ids[0];
  await page.locator(`#detail-duplicates [data-open-receipt="${related}"]`).click();
  await page.waitForFunction(id=>state.active===id,related);
  await page.locator('[data-action="back-to-ledger"]').click();
  await page.locator('#ledger-search').fill('');
  await page.waitForFunction(()=>document.querySelectorAll('#ledger-rows tr').length===100);
  checks.push('duplicate warning remains visible in receipt details and opens related originals');

  // Composition events exercise real DOM replacement/focus behavior, without an OS IME.
  const composing=await page.evaluate(async()=>{
    const input=document.querySelector('#ledger-search'),before=document.querySelector('#ledger-rows').innerHTML;
    input.focus();input.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true}));
    input.value='V002.pdf';input.dispatchEvent(new InputEvent('input',{bubbles:true,isComposing:true,data:'V002.pdf'}));
    await new Promise(resolve=>setTimeout(resolve,260));
    const stable=document.querySelector('#ledger-search')===input&&document.querySelector('#ledger-rows').innerHTML===before;
    input.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'V002.pdf'}));
    return stable&&document.querySelector('#ledger-search')===input&&document.activeElement===input;
  });
  assert.equal(composing,true);await page.waitForFunction(()=>document.querySelectorAll('#ledger-rows tr').length===1);
  await page.locator('#ledger-search').fill('');await page.waitForFunction(()=>document.querySelectorAll('#ledger-rows tr').length===100);
  checks.push('Japanese composition preserves the input node, focus, and rows until composition ends');
  const firstPage=rows.find(r=>r.source_document?.page===1),secondPage=rows.find(r=>r.source_document?.page===2);
  await page.locator('#ledger-search').fill(firstPage.original_name);await page.locator(`[data-open-receipt="${firstPage.id}"]`).click();
  await page.locator('#review-form [name="purpose"]').fill('制作業務で使用');await page.locator('#review-form [name="business_percent"]').fill('100');await page.locator('#review-form [name="payment"]').selectOption('owner');
  await page.locator('[data-action="confirm-next"]').click();await page.waitForFunction(id=>state.active===id,secondPage.id);
  assert.equal((await api('receipts/'+firstPage.id)).status,'confirmed');
  checks.push('confirm-and-next follows page1 to page2 within the same split PDF');
  await page.locator('[data-view="ledger"]').click();await page.locator('#ledger-search').fill('');await page.waitForFunction(()=>document.querySelectorAll('#ledger-rows tr').length===100);
  // Reversible wrong-import remedy retains immutable source files.
  if(await page.locator('[data-action="clear-selection"]').isVisible())await page.locator('[data-action="clear-selection"]').click();
  const chosen=[...await page.locator('[data-ledger-select]').evaluateAll(nodes=>nodes.slice(0,2).map(node=>node.dataset.ledgerSelect))];
  for(const id of chosen)await page.locator(`[data-ledger-select="${id}"]`).check();
  page.once('dialog',dialog=>dialog.accept());await page.locator('[data-action="exclude-selected"]').click();
  await page.waitForFunction(async ids=>{const rows=(await(await fetch('/api/receipts')).json()).receipts;return ids.every(id=>rows.find(r=>r.id===id)?.status==='excluded');},chosen);
  await page.waitForFunction(()=>document.querySelector('#batch-result')?.innerText.includes('2件を対象外にしました'));
  await page.locator('#ledger-filter').selectOption('excluded');await page.waitForFunction(()=>document.querySelectorAll('[data-ledger-select]').length===2);
  for(const id of chosen)await page.locator(`[data-ledger-select="${id}"]`).check();
  page.once('dialog',dialog=>dialog.accept());await page.locator('[data-action="restore-selected"]').click();
  await page.waitForFunction(async ids=>{const rows=(await(await fetch('/api/receipts')).json()).receipts;return ids.every(id=>rows.find(r=>r.id===id)?.status==='review');},chosen);
  await page.waitForFunction(()=>document.querySelector('#batch-result')?.innerText.includes('2件を確認待ちへ戻しました'));
  await page.locator('#ledger-filter').selectOption('all');
  checks.push('bulk exclude and restore are reversible and retain originals');

  const blocked=rows.find(r=>r.original_name==='V019.pdf');
  await page.locator('#ledger-search').fill(blocked.original_name);await page.locator(`[data-open-receipt="${blocked.id}"]`).click();
  assert.match(await page.locator('#review-form').innerText(),/この明細は確認済みにできません/);
  assert.match(await page.locator('#review-form').innerText(),/返金/);
  assert.equal(await page.locator('#review-form [type="submit"]').isDisabled(),true);
  await page.locator('[data-view="ledger"]').click();await page.locator('#ledger-search').fill('');
  checks.push('refund confirmation is disabled with the exact reason visible on the form');


  const folder=path.join(out,'folder-input'),nested=path.join(folder,'nested');await fs.mkdir(nested,{recursive:true});await fs.copyFile(root+'/samples/receipts-100-v2/thumbnails/V001.jpg',path.join(folder,'a.jpg'));await fs.copyFile(root+'/samples/receipts-100-v2/thumbnails/V002.jpg',path.join(nested,'b.jpg'));await fs.writeFile(path.join(folder,'対象外.txt'),'unsupported');
  await page.locator('#upload-top').click();await page.locator('#folder-input').setInputFiles(folder);await waitImported(3);await waitComplete(202);assert.match(await page.locator('#import-progress').innerText(),/未対応形式1ファイル/);checks.push('folder selection includes nested files and explains unsupported formats');
  await page.locator('[data-import-action="dismiss"]').click();
  const original=rows.find(r=>r.original_name==='V001.pdf');await page.evaluate(async id=>{const raw=await(await fetch('/api/receipts/'+id+'/original')).arrayBuffer();const dt=new DataTransfer();dt.items.add(new File([raw],'V001.pdf',{type:'application/pdf'}));document.dispatchEvent(new DragEvent('drop',{dataTransfer:dt,bubbles:true,cancelable:true}));},original.id);
  await page.locator('#import-dialog').waitFor({state:'visible'});await page.locator('[name="pdf-mode"][value="document"]').check();await page.locator('#start-import').click();await waitImported(1);assert.match(await page.locator('#import-progress').innerText(),/重複1件/);checks.push('file drop asks PDF mode and skips the existing original');
  await page.setViewportSize({width:375,height:900});await snapshot('06-mobile-ledger');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  await page.locator('#upload-top').click();await snapshot('07-mobile-import');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.locator('#import-dialog .dialog-heading button').click();
  checks.push('375px mobile layout and import dialog');
  await page.setViewportSize({width:1365,height:1000});await page.locator('[data-view="settings"]').click();await page.locator('#review-before-ledger').check();await page.waitForFunction(async()=>(await(await fetch('/api/config')).json()).review_before_ledger);await page.reload({waitUntil:'networkidle'});assert.equal(await page.locator('#page-title').innerText(),'領収書の確認');
  await page.locator('[data-view="settings"]').click();await page.locator('#review-before-ledger').uncheck();await page.waitForFunction(async()=>!(await(await fetch('/api/config')).json()).review_before_ledger);
  checks.push('optional review-first setting automatically persists');

  await page.locator('[data-view="ledger"]').click();
  if(await page.locator('[data-action="clear-selection"]').isVisible())await page.locator('[data-action="clear-selection"]').click();
  if(await page.locator('[data-action="clear-issue-filter"]').isVisible())await page.locator('[data-action="clear-issue-filter"]').click();
  await page.locator('#ledger-search').fill('');await page.locator('#ledger-filter').selectOption('all');
  await page.locator('#select-ledger-all').check();
  await page.locator('[data-action="ledger-next"]').click();await page.locator('#select-ledger-all').check();
  assert.ok(await page.evaluate(()=>state.selected.size)>100);
  await page.locator('#batch-purpose').fill('制作業務で使用');await page.locator('#batch-percent').fill('100');
  await page.locator('#batch-payment').selectOption('owner');await page.locator('#batch-tax').selectOption('outside');
  const beforeBatch=(await api('receipts')).receipts.filter(r=>r.status==='confirmed').length;
  let reviewCalls=0;
  await page.route('**/api/ledger/review',async route=>{
    if(++reviewCalls===2)return route.abort();
    await route.continue();
  });
  await page.locator('[data-action="review-ledger"]').click();await page.locator('#review-ack').check();await page.locator('#confirm-batch').click();
  await page.waitForFunction(()=>document.querySelector('#batch-result').innerText.includes('通信が中断したため'));
  await page.unroute('**/api/ledger/review');
  assert.equal(reviewCalls,2);assert.equal(await page.locator('#review-dialog').isVisible(),false);
  assert.ok(await page.evaluate(()=>state.batchReport.count)>0);
  assert.ok((await api('receipts')).receipts.filter(r=>r.status==='confirmed').length>beforeBatch);
  assert.ok(await page.evaluate(()=>state.selected.size)>0);
  assert.ok(await page.locator('#batch-result .batch-issue').count()>0);
  assert.equal((await api('receipts')).receipts.length,202);
  await snapshot('07b-interrupted-batch');await page.locator('[data-action="clear-selection"]').click();
  checks.push('a failed second100-row batch retains saved rows, reports partial success and names remaining rows');

  if(await page.locator('[data-import-action="dismiss"]').isVisible())await page.locator('[data-import-action="dismiss"]').click();

  const guarded=await page.evaluate(()=>{
    const record=state.records.find(r=>r.status==='confirmed'),original=record.draft.date;
    try{record.draft.date=null;summaryFor();state.view='accounts';render();return true;}
    finally{record.draft.date=original;state.view='ledger';render();}
  });
  assert.equal(guarded,true);checks.push('missing date in a simulated legacy confirmed row does not crash summaries');
  const fresh=(await api('receipts')).receipts;
  let calls=0;
  await page.route('**/api/receipts',async route=>{
    const turn=++calls,rows=structuredClone(fresh);rows[0].draft.merchant=turn===1?'stale-response':'new-response';
    if(turn===1)await new Promise(resolve=>setTimeout(resolve,250));
    await route.fulfill({json:{receipts:rows}});
  });
  await page.evaluate(()=>Promise.all([loadReceipts(),loadReceipts()]));
  assert.equal(await page.evaluate(()=>state.records[0].draft.merchant),'new-response');
  await page.unroute('**/api/receipts');await page.evaluate(()=>loadReceipts());
  checks.push('a delayed old receipt response cannot overwrite a newer refresh');
  const oldConfig=await api('config');delete oldConfig.limits.max_split_pages;
  await page.route('**/api/config',route=>route.fulfill({json:oldConfig}));await page.reload({waitUntil:'networkidle'});
  assert.match(await page.locator('#main-content').innerText(),/アプリ本体を再起動してください/);assert.equal(await page.locator('#upload-top').isDisabled(),true);
  await page.unroute('**/api/config');await page.reload({waitUntil:'networkidle'});
  checks.push('old backend compatibility guard guides a restart before allowing uploads');

  // Rendering stress only: 2000 rejected empty files create no HTTP import or OCR work.
  const stress=await page.evaluate(()=>{
    const started=performance.now();
    RyoshuImport.addFiles(Array.from({length:2000},(_,i)=>new File([],`empty-${i}.jpg`,{type:'image/jpeg'})));
    return {milliseconds:performance.now()-started,closedRows:document.querySelectorAll('.import-results li').length};
  });
  assert.equal(stress.closedRows,0);assert.ok(stress.milliseconds<5000,JSON.stringify(stress));
  await openResults();await page.waitForFunction(()=>document.querySelectorAll('.import-results li').length===100);
  assert.match(await page.locator('.import-result-controls').innerText(),/1 \/ 20ページ/);
  await page.locator('[data-import-action="result-next"]').click();assert.match(await page.locator('.import-result-controls').innerText(),/2 \/ 20ページ/);
  assert.equal(await page.locator('.import-results li').count(),100);
  await snapshot('08-2000-result-pagination');await page.locator('[data-import-action="dismiss"]').click();
  checks.push('2000-file result rendering is lazy and paged to100; this is not a2000-document OCR test');

  assert.deepEqual(errors,[]);
  const result={status:'passed',scope:'Real browser/HTTP/persistence/CSV; OCR and model observations replayed by QA test double, not new inference',checks,errors,receipt_count:202,render_stress:stress,source_sha256:await sourceHashes()};await fs.writeFile(path.join(out,'record.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}catch(error){if(page)await snapshot('FAILED').catch(()=>{});await fs.writeFile(path.join(out,'failure.txt'),error.stack);throw error;}finally{await browser?.close();await stopServer();}
