// Real UI/HTTP and encrypted storage; saved fictional observations, no new inference.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
const {chromium}=await import(process.env.RYOSHU_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.resolve(process.env.RYOSHU_UX_OUTPUT||path.join(root,'.local/ux-qa',new Date().toISOString().replace(/[:.]/g,'-')));
await fs.mkdir(out,{recursive:true});
const checks=[],errors=[],logs=[];
let server,serverExit,browser,page,address;
const api=route=>page.evaluate(async route=>(await(await RyoshuSecurity.fetch('/api/'+route)).json()),route);
async function sourceHashes(){const result={};for(const n of ['web/app.js','web/import.js','web/processing.js','web/security.js','web/index.html','web/style.css','receipt_ledger/server.py','scripts/serve_browser_qa.py','scripts/ux_workflow_smoke.mjs'])result[n]=createHash('sha256').update(await fs.readFile(path.join(root,n))).digest('hex');return result;}
async function go(view){await page.locator(`.nav-item[data-view="${view}"]`).first().click();await page.waitForFunction(v=>state.view===v,view);}
async function waitForRecords(count){await page.waitForFunction(async count=>{const rows=(await(await RyoshuSecurity.fetch('/api/receipts')).json()).receipts;return rows.length===count&&rows.every(r=>!['queued','processing'].includes(r.status));},count,{timeout:30000});await page.waitForFunction(count=>state.records.length===count&&state.records.every(r=>!['queued','processing'].includes(r.status)),count,{timeout:15000});}
async function snapshot(name){
  // Full-page captures must load lazy originals before judging preview quality.
  for(const img of await page.locator('.result-image img:visible').all()){
    await img.scrollIntoViewIfNeeded();
    await img.evaluate(n=>n.complete&&n.naturalWidth>0?undefined:new Promise((resolve,reject)=>{
      n.addEventListener('load',resolve,{once:true});
      n.addEventListener('error',()=>reject(new Error('Original preview failed to load')),{once:true});
    }));
  }
  await page.evaluate(()=>scrollTo(0,0));
  await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
}
async function waitPreview(count){await page.waitForFunction(count=>document.querySelector('#export-result')?.innerText.includes(`${count}件 / ${count}行`),count,{timeout:10000});}
async function assertFinishedPanelsHidden(view){
  assert.equal(await page.locator('#processing-status').getAttribute('data-state'),'finished',`${view} fixture is not a finished successful batch`);
  assert.equal(await page.locator('#import-progress').evaluate(n=>n.classList.contains('has-issues')||n.classList.contains('is-saving')),false,`${view} fixture still has import issues or active work`);
  for(const selector of ['#import-progress','#processing-status']){
    await page.locator(selector).waitFor({state:'hidden'});
    assert.equal(await page.locator(selector).isVisible(),false,`${view} is obstructed by ${selector}`);
  }
  assert.equal(await page.locator('#page-title').evaluate(n=>{const box=n.getBoundingClientRect();return box.top>=0&&box.bottom<=innerHeight;}),true,`${view} first heading is outside the initial viewport`);
  assert.equal(await page.locator('#main-content').evaluate(n=>n.getBoundingClientRect().top<innerHeight),true,`${view} content begins below the first viewport`);
}
try{
  server=spawn(process.env.RYOSHU_QA_PYTHON||'python',[path.join(root,'scripts/serve_browser_qa.py'),'--data-dir',path.join(out,'data')],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  serverExit=new Promise(resolve=>server.once('exit',(code,signal)=>resolve({code,signal})));
  let pending='';
  address=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('QA server startup timed out')),30000);
    server.once('error',e=>{clearTimeout(timer);reject(e);});
    server.once('exit',code=>{clearTimeout(timer);reject(new Error('QA server exited:'+code));});
    server.stderr.on('data',chunk=>logs.push(chunk.toString()));
    server.stdout.on('data',chunk=>{pending+=chunk.toString();let i;while((i=pending.indexOf('\n'))>=0){const line=pending.slice(0,i);pending=pending.slice(i+1);try{const data=JSON.parse(line);if(data.qa_url){clearTimeout(timer);resolve(data);}}catch{logs.push(line+'\n');}}});
  });
  browser=await chromium.launch({headless:true,...(process.env.RYOSHU_BROWSER_CHANNEL?{channel:process.env.RYOSHU_BROWSER_CHANNEL}:{})});
  page=await browser.newPage({viewport:{width:1365,height:1000},acceptDownloads:true});
  page.on('pageerror',e=>errors.push(e.message));
  const hashes=await sourceHashes();
  // Reproduce normal first use after setup, even on CI without local AI models.
  // This appearance fixture changes only readiness/banner fields; no inference.
  await page.route('**/api/health',async route=>{
    const response=await route.fetch();const data=await response.json();
    await route.fulfill({response,json:{...data,environment_label:'',model_ready:true,model_issue:''}});
  });
  await page.goto(address.open_url,{waitUntil:'networkidle'});
  await page.locator('#choose-files').waitFor();
  for(const [width,height] of [[1366,768],[1093,614]]){
    await page.setViewportSize({width,height});
    const start=await page.locator('#start-import').boundingBox();
    assert.ok(start&&start.y>=0&&start.y+start.height<=height,`${width}x${height} first-use Start requires scrolling`);
    await snapshot(`first-use-${width}x${height}`);
  }
  checks.push('first-use file selection and Start fit the initial1366x768 and125%-equivalent1093x614 viewports');
  await page.unroute('**/api/health');
  await page.setViewportSize({width:1365,height:1000});
  await page.reload({waitUntil:'networkidle'});
  const sample=id=>path.join(root,'samples/receipts-100-v2/individual',id+'.pdf');
  await page.locator('#file-input').setInputFiles(['V001','V021','V023','V044','V045'].map(sample));
  assert.equal((await api('receipts')).receipts.length,0);
  assert.match(await page.locator('#import-selection').innerText(),/5ファイル選択中/);
  await page.locator('#start-import').click();
  await page.waitForFunction(()=>Math.abs(scrollY)<=1&&document.activeElement?.id==='page-title');
  checks.push('starting an import brings progress into view once without scrolling on polling updates');
  await waitForRecords(5);
  await page.locator('#processing-status a[data-view="results"]').click();
  await page.locator('.results-table').waitFor();
  assert.deepEqual(await page.locator('.results-table th').allTextContents(),['原本','読み取った内容','勘定科目']);
  assert.equal(await page.locator('.results-table tbody tr').count(),5);
  await page.waitForFunction(()=>[...document.querySelectorAll('.result-image img')].some(n=>n.naturalWidth>0));
  checks.push('five PDFs remain staged until Start, then results present originals/read contents/accounts');
  assert.equal((await api('receipts')).receipts.every(r=>!r.error&&!['queued','processing','error'].includes(r.status)),true,'Successful progress visibility fixture contains an unfinished or failed receipt');
  await assertFinishedPanelsHidden('results');
  await snapshot('01-results-desktop');
  await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));
  assert.ok(await page.evaluate(()=>scrollY)>100,'Results fixture did not reach a meaningful outer-page scroll position');
  // Locator.click() would scroll an off-screen nav into view before the app
  // handles it, hiding the preserved-scroll regression this check exercises.
  await page.locator('.nav-item[data-view="ledger"]').evaluate(n=>n.click());
  await page.waitForFunction(()=>state.view==='ledger'&&Math.abs(scrollY)<=1&&document.activeElement?.id==='page-title');
  checks.push('CSV navigation from the results page bottom resets outer scroll and focuses its first heading');
  await assertFinishedPanelsHidden('ledger');
  checks.push('successful finished import and processing cards stay hidden above results and CSV content');
  await page.locator('#month-filter').fill('2024-04');
  await page.waitForFunction(()=>document.querySelector('#ledger-rows tr')?.innerText.includes('テスト光'));
  assert.equal(await page.locator('#ledger-rows tr').count(),1);
  await waitPreview(1);
  assert.equal(await page.locator('#month-filter').inputValue(),'2024-04');
  assert.equal(await page.locator('#month-filter').evaluate(n=>n===document.activeElement),true);
  assert.equal(await page.locator('[data-action="all-months"]').getAttribute('aria-pressed'),'false');
  checks.push('month input keeps focus and the CSV preview and ledger share the selected period');
  await page.locator('#export-profile').selectOption('mf');
  await page.waitForFunction(()=>state.exportProfile==='mf');
  assert.equal(await page.locator('#export-profile').evaluate(n=>n===document.activeElement),true);
  assert.equal(await page.locator('#month-filter').inputValue(),'2024-04');
  await page.locator('#export-profile').selectOption('generic');await waitPreview(1);
  assert.equal(await page.locator('#export-profile').evaluate(n=>n===document.activeElement),true);
  checks.push('format changes preserve keyboard focus and the selected accounting period');
  await page.setViewportSize({width:1365,height:650});
  await page.locator('#ledger-search').fill('テスト光');
  await page.waitForFunction(()=>state.ledgerSearch==='テスト光'&&document.querySelectorAll('#ledger-rows tr').length===1);
  const detailTrigger=page.locator('#ledger-rows [data-open-receipt]').first();
  const receiptId=await detailTrigger.getAttribute('data-open-receipt');
  await page.evaluate(()=>scrollTo(0,Math.min(240,document.documentElement.scrollHeight-innerHeight)));
  const origin=await page.evaluate(()=>({scrollY,month:document.querySelector('#month-filter').value,search:document.querySelector('#ledger-search').value}));
  assert.ok(origin.scrollY>100,'CSV fixture did not reach a meaningful outer-page scroll position');
  await detailTrigger.evaluate(n=>n.click());
  await page.locator('#review-form').waitFor();
  await page.locator('[data-action="back-from-detail"]').click();
  await page.waitForFunction(({top,id})=>state.view==='ledger'&&Math.abs(scrollY-top)<=2&&document.activeElement?.dataset.openReceipt===id,{top:origin.scrollY,id:receiptId});
  assert.equal(await page.locator('#month-filter').inputValue(),origin.month);
  assert.equal(await page.locator('#ledger-search').inputValue(),origin.search);
  assert.equal(await page.locator('#ledger-rows tr').count(),1);
  assert.equal(await page.locator('.nav-item[data-view="ledger"]').getAttribute('aria-current'),'page');
  checks.push('detail opened from CSV returns to CSV with its period, search, outer scroll and originating row focus');
  await page.locator('#ledger-search').fill('');
  await page.waitForFunction(()=>state.ledgerSearch==='');
  await page.setViewportSize({width:1365,height:1000});
  await page.locator('#upload-top').click();
  await page.locator('#import-dialog[open]').waitFor();
  await page.locator('#file-input').setInputFiles(sample('V055'));
  await page.locator('#start-import').click();await waitForRecords(6);
  await page.locator('.results-table').waitFor();
  assert.equal(await page.locator('.results-table tbody tr').count(),6);
  await go('ledger');
  assert.equal(await page.locator('#month-filter').inputValue(),'2024-04');await waitPreview(1);
  assert.equal(await page.locator('#ledger-rows tr').count(),1);
  checks.push('additional import from CSV returns to results and preserves the previous CSV/list period');
  await page.locator('[data-action="all-months"]').click();await waitPreview(6);
  assert.equal(await page.locator('#month-filter').inputValue(),'');
  assert.equal(await page.locator('[data-action="all-months"]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('[data-action="all-months"]').evaluate(n=>n===document.activeElement),true);
  checks.push('all-period control exposes its selected state, retains focus and updates the six-row preview');
  await go('results');
  await page.locator('#results-pending').check();
  assert.equal(await page.locator('#results-pending').evaluate(n=>n===document.activeElement),true);
  assert.equal(await page.locator('.nav-item[data-view="results"]').getAttribute('aria-current'),'page');
  await page.locator('[data-open-receipt]').first().click();
  await page.locator('#review-form').waitFor();
  assert.equal(await page.locator('.nav-item[data-view="results"]').getAttribute('aria-current'),'page');
  await go('results');
  checks.push('result filter retains focus and the result navigation stays current while editing');
  const widths=[375,850,851,1000,1001];
  for(const width of widths){
    await page.setViewportSize({width,height:1000});
    for(const view of ['home','results','ledger']){
      await go(view);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${width}px ${view} overflows`);
      for(const link of await page.locator('.nav-item:visible').all())assert.ok((await link.boundingBox()).height>=44,'Navigation target under44px');
      await snapshot(`responsive-${width}-${view}`);
    }
  }
  checks.push('home/results/CSV avoid body overflow at375/850/851/1000/1001px with44px primary navigation targets');
  assert.deepEqual(await sourceHashes(),hashes,'UI source changed during run; rerun stable candidate');
  assert.deepEqual(errors,[]);
  const proof={status:'passed',scope:'Real browser/HTTP/encrypted persistence. Saved fictional observations replayed; not fresh OCR/AI. First-use viewport checks set only model readiness/banner fields in the health response to reproduce the appearance after setup, including on CI without local models. Actual health response is restored before importing.',checks,errors,receipt_count:6,breakpoint_widths:widths,source_sha256:hashes};
  await fs.writeFile(path.join(out,'record.json'),JSON.stringify(proof,null,2)+'\n');
  console.log(JSON.stringify({status:proof.status,checks:checks.length,out}));
}finally{
  if(browser)await browser.close();
  if(address)await fetch(address.qa_url+'/_qa/stop',{method:'POST',headers:{'x-qa-token':address.stop_token}}).catch(()=>{});
  if(serverExit){let timer;const result=await Promise.race([serverExit,new Promise(resolve=>{timer=setTimeout(()=>resolve(null),10000);})]);clearTimeout(timer);if(!result)server.kill();}
  await fs.writeFile(path.join(out,'server.log'),logs.join(''));
}
