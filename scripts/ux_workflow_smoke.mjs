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
// Finite budgets: every ordinary wait ends, and the whole scenario ends, with a diagnostic instead of a silent hang.
const BUDGET={action:15000,navigation:30000,images:30000,imagePoll:100,server:30000,scenario:Number(process.env.RYOSHU_UX_SCENARIO_MS)||360000,
  browserClose:15000,stopRequest:5000,serverExit:10000,serverKill:5000};
const checks=[],errors=[],logs=[];
let server,serverExit,browser,page,address,hashes,phase='start',images=null;
const api=route=>page.evaluate(async route=>(await(await RyoshuSecurity.fetch('/api/'+route)).json()),route);
async function sourceHashes(){const result={};for(const n of ['web/app.js','web/import.js','web/processing.js','web/security.js','web/index.html','web/style.css','receipt_ledger/server.py','scripts/serve_browser_qa.py','scripts/ux_workflow_smoke.mjs'])result[n]=createHash('sha256').update(await fs.readFile(path.join(root,n))).digest('hex');return result;}
function within(promise,ms){let timer;return Promise.race([promise,new Promise(resolve=>{timer=setTimeout(()=>resolve('timed_out'),ms);})]).finally(()=>clearTimeout(timer));}
// Diagnostics carry only the phase, controlled image fields, the check count and source hashes; never tokens or paths.
function redact(text){let s=String(text);for(const v of [...Object.values(address||{}),out,root])if(typeof v==='string'&&v)s=s.split(v).join('<redacted>');return s.replace(/#open=[^\s'"]*/g,'#open=<redacted>').slice(0,500);}
async function checkpoint(status,error){
  await fs.writeFile(path.join(out,'checkpoint.json'),JSON.stringify({status,phase,checks_completed:checks.length,images,source_sha256:hashes??null,...(error?{error:redact(error.message??error)}:{})},null,2)+'\n');
}
async function pass(text){checks.push(text);await checkpoint('running');}
function step(name){phase=name;}
async function go(view){await page.locator(`.nav-item[data-view="${view}"]`).first().click();await page.waitForFunction(v=>state.view===v,view);}
async function waitForRecords(count){await page.waitForFunction(async count=>{const rows=(await(await RyoshuSecurity.fetch('/api/receipts')).json()).receipts;return rows.length===count&&rows.every(r=>!['queued','processing'].includes(r.status));},count,{timeout:30000});await page.waitForFunction(count=>state.records.length===count&&state.records.every(r=>!['queued','processing'].includes(r.status)),count,{timeout:15000});}
/* image-readiness:begin */
// Re-resolves the CURRENT visible originals on every poll, so a node replaced after a re-render is judged afresh and a
// node that already failed before we looked is reported broken (no load/error listener can miss an earlier event).
// A protected original has no src until its authenticated fetch assigns a blob URL; complete with naturalWidth 0 then
// means "not assigned yet" (pending, bounded by the timeout), not a failed load. Only an attempted source can be broken.
// Diagnostics are presence flags only; URLs are never returned.
function imageReadiness(){
  return [...document.querySelectorAll('.result-image img')].flatMap((n,index)=>{
    const box=n.getBoundingClientRect();
    if(box.width<=0||box.height<=0||getComputedStyle(n).visibility==='hidden')return [];
    const hasSrc=!!n.getAttribute('src'),hasCurrentSrc=!!n.currentSrc;
    const status=!(hasSrc||hasCurrentSrc)||!n.complete?'pending':n.naturalWidth>0?'loaded':'broken';
    return [{index,row:n.closest('tr')?.sectionRowIndex??null,status,hasSrc,hasCurrentSrc,lazy:n.loading==='lazy',privateSrc:n.hasAttribute('data-private-src')}];
  });
}
async function waitOriginals(page,{timeoutMs,pollMs},onStatus=()=>{}){
  const deadline=Date.now()+timeoutMs;
  for(;;){
    const current=await page.evaluate(imageReadiness);
    onStatus(current);
    const broken=current.filter(i=>i.status==='broken');
    if(broken.length)throw new Error('Original preview failed to load: '+JSON.stringify(broken));
    const pending=current.filter(i=>i.status==='pending');
    if(!pending.length)return current;
    const left=deadline-Date.now();
    if(left<=0)throw new Error(`Original previews not loaded within ${timeoutMs}ms: `+JSON.stringify(pending));
    // Lazy originals load once scrolled into view; a node replaced meanwhile is simply re-resolved on the next poll.
    await page.locator('.result-image img').nth(pending[0].index).scrollIntoViewIfNeeded({timeout:Math.max(1,Math.min(left,pollMs*10))}).catch(()=>{});
    await page.waitForTimeout(Math.max(1,Math.min(pollMs,deadline-Date.now())));
  }
}
/* image-readiness:end */
async function snapshot(name){
  step('snapshot:'+name);
  // Full-page captures must load lazy originals before judging preview quality.
  await waitOriginals(page,{timeoutMs:BUDGET.images,pollMs:BUDGET.imagePoll},current=>{images=current;});
  await page.evaluate(()=>scrollTo(0,0));
  await page.screenshot({path:path.join(out,name+'.png'),fullPage:true,timeout:BUDGET.navigation});
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
async function scenario(){
  hashes=await sourceHashes();
  step('server_start');
  server=spawn(process.env.RYOSHU_QA_PYTHON||'python',[path.join(root,'scripts/serve_browser_qa.py'),'--data-dir',path.join(out,'data')],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  serverExit=new Promise(resolve=>server.once('exit',(code,signal)=>resolve({code,signal})));
  let pending='';
  address=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('QA server startup timed out')),BUDGET.server);
    server.once('error',e=>{clearTimeout(timer);reject(e);});
    server.once('exit',code=>{clearTimeout(timer);reject(new Error('QA server exited:'+code));});
    server.stderr.on('data',chunk=>logs.push(chunk.toString()));
    server.stdout.on('data',chunk=>{pending+=chunk.toString();let i;while((i=pending.indexOf('\n'))>=0){const line=pending.slice(0,i);pending=pending.slice(i+1);try{const data=JSON.parse(line);if(data.qa_url){clearTimeout(timer);resolve(data);}}catch{logs.push(line+'\n');}}});
  });
  step('browser_launch');
  browser=await chromium.launch({headless:true,timeout:BUDGET.navigation,...(process.env.RYOSHU_BROWSER_CHANNEL?{channel:process.env.RYOSHU_BROWSER_CHANNEL}:{})});
  page=await browser.newPage({viewport:{width:1365,height:1000},acceptDownloads:true});
  page.setDefaultTimeout(BUDGET.action);
  page.setDefaultNavigationTimeout(BUDGET.navigation);
  page.on('pageerror',e=>errors.push(e.message));
  // Reproduce normal first use after setup, even on CI without local AI models.
  // This appearance fixture changes only readiness/banner fields; no inference.
  step('first_use');
  await page.route('**/api/health',async route=>{
    const response=await route.fetch({timeout:BUDGET.action});const data=await response.json();
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
  await pass('first-use file selection and Start fit the initial1366x768 and125%-equivalent1093x614 viewports');
  await page.unroute('**/api/health');
  await page.setViewportSize({width:1365,height:1000});
  await page.reload({waitUntil:'networkidle'});
  step('import_five');
  const sample=id=>path.join(root,'samples/receipts-100-v2/individual',id+'.pdf');
  await page.locator('#file-input').setInputFiles(['V001','V021','V023','V044','V045'].map(sample));
  assert.equal((await api('receipts')).receipts.length,0);
  assert.match(await page.locator('#import-selection').innerText(),/5ファイルを選択中（PDF 5ファイル）。「読み取りを開始」を押すまで処理しません。/);
  await page.locator('#start-import').click();
  await page.waitForFunction(()=>Math.abs(scrollY)<=1&&document.activeElement?.id==='page-title');
  await pass('starting an import brings progress into view once without scrolling on polling updates');
  step('results_five');
  await waitForRecords(5);
  await page.locator('#processing-status a[data-view="results"]').click();
  await page.locator('.results-table').waitFor();
  assert.deepEqual(await page.locator('.results-table th').allTextContents(),['原本','読み取った内容','勘定科目']);
  assert.equal(await page.locator('.results-table tbody tr').count(),5);
  await page.waitForFunction(()=>[...document.querySelectorAll('.result-image img')].some(n=>n.naturalWidth>0));
  await pass('five PDFs remain staged until Start, then results present originals/read contents/accounts');
  assert.equal((await api('receipts')).receipts.every(r=>!r.error&&!['queued','processing','error'].includes(r.status)),true,'Successful progress visibility fixture contains an unfinished or failed receipt');
  await assertFinishedPanelsHidden('results');
  await snapshot('01-results-desktop');
  step('results_to_ledger_scroll');
  await page.evaluate(()=>scrollTo(0,document.documentElement.scrollHeight));
  assert.ok(await page.evaluate(()=>scrollY)>100,'Results fixture did not reach a meaningful outer-page scroll position');
  // Locator.click() would scroll an off-screen nav into view before the app
  // handles it, hiding the preserved-scroll regression this check exercises.
  await page.locator('.nav-item[data-view="ledger"]').evaluate(n=>n.click());
  await page.waitForFunction(()=>state.view==='ledger'&&Math.abs(scrollY)<=1&&document.activeElement?.id==='page-title');
  await pass('CSV navigation from the results page bottom resets outer scroll and focuses its first heading');
  await assertFinishedPanelsHidden('ledger');
  await pass('successful finished import and processing cards stay hidden above results and CSV content');
  step('ledger_month');
  await page.locator('#month-filter').fill('2024-04');
  await page.waitForFunction(()=>document.querySelector('#ledger-rows tr')?.innerText.includes('テスト光'));
  assert.equal(await page.locator('#ledger-rows tr').count(),1);
  await waitPreview(1);
  assert.equal(await page.locator('#month-filter').inputValue(),'2024-04');
  assert.equal(await page.locator('#month-filter').evaluate(n=>n===document.activeElement),true);
  assert.equal(await page.locator('[data-action="all-months"]').getAttribute('aria-pressed'),'false');
  await pass('month input keeps focus and the CSV preview and ledger share the selected period');
  step('ledger_format');
  await page.locator('#export-profile').selectOption('mf');
  await page.waitForFunction(()=>state.exportProfile==='mf');
  assert.equal(await page.locator('#export-profile').evaluate(n=>n===document.activeElement),true);
  assert.equal(await page.locator('#month-filter').inputValue(),'2024-04');
  await page.locator('#export-profile').selectOption('generic');await waitPreview(1);
  assert.equal(await page.locator('#export-profile').evaluate(n=>n===document.activeElement),true);
  await pass('format changes preserve keyboard focus and the selected accounting period');
  step('ledger_detail_return');
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
  await pass('detail opened from CSV returns to CSV with its period, search, outer scroll and originating row focus');
  step('additional_import');
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
  await pass('additional import from CSV returns to results and preserves the previous CSV/list period');
  step('all_months');
  await page.locator('[data-action="all-months"]').click();await waitPreview(6);
  assert.equal(await page.locator('#month-filter').inputValue(),'');
  assert.equal(await page.locator('[data-action="all-months"]').getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('[data-action="all-months"]').evaluate(n=>n===document.activeElement),true);
  await pass('all-period control exposes its selected state, retains focus and updates the six-row preview');
  step('results_filter_detail');
  await go('results');
  await page.locator('#results-pending').check();
  assert.equal(await page.locator('#results-pending').evaluate(n=>n===document.activeElement),true);
  assert.equal(await page.locator('.nav-item[data-view="results"]').getAttribute('aria-current'),'page');
  await page.locator('[data-open-receipt]').first().click();
  await page.locator('#review-form').waitFor();
  assert.equal(await page.locator('.nav-item[data-view="results"]').getAttribute('aria-current'),'page');
  await go('results');
  await pass('result filter retains focus and the result navigation stays current while editing');
  const widths=[375,850,851,1000,1001];
  for(const width of widths){
    await page.setViewportSize({width,height:1000});
    for(const view of ['home','results','ledger']){
      step(`responsive:${width}:${view}`);
      await go(view);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${width}px ${view} overflows`);
      for(const link of await page.locator('.nav-item:visible').all())assert.ok((await link.boundingBox()).height>=44,'Navigation target under44px');
      await snapshot(`responsive-${width}-${view}`);
    }
  }
  await pass('home/results/CSV avoid body overflow at375/850/851/1000/1001px with44px primary navigation targets');
  step('final_record');
  assert.deepEqual(await sourceHashes(),hashes,'UI source changed during run; rerun stable candidate');
  assert.deepEqual(errors,[]);
  const proof={status:'passed',scope:'Real browser/HTTP/encrypted persistence. Saved fictional observations replayed; not fresh OCR/AI. First-use viewport checks set only model readiness/banner fields in the health response to reproduce the appearance after setup, including on CI without local models. Actual health response is restored before importing.',checks,errors,receipt_count:6,breakpoint_widths:widths,source_sha256:hashes};
  await fs.writeFile(path.join(out,'record.json'),JSON.stringify(proof,null,2)+'\n');
  step('done');
  await checkpoint('passed');
  console.log(JSON.stringify({status:proof.status,checks:checks.length,out}));
}
let watchdog,failure=null,lingering=false;
const run=scenario();
run.catch(()=>{});   // after a watchdog expiry the abandoned scenario rejects once cleanup closes its browser
try{
  await Promise.race([run,new Promise((_,reject)=>{watchdog=setTimeout(()=>reject(new Error(`Scenario watchdog expired after ${BUDGET.scenario}ms`)),BUDGET.scenario);})]);
}catch(e){failure=e;}
finally{
  clearTimeout(watchdog);
  if(failure){
    process.exitCode=1;
    await checkpoint('failed',failure).catch(()=>{});
    console.error(JSON.stringify({status:'failed',phase,checks:checks.length,images,error:redact(failure.message??failure)}));
  }
  // Owned cleanup only: this browser, this QA server's token-guarded stop, then exactly the spawned server process.
  if(browser&&await within(browser.close().catch(()=>{}),BUDGET.browserClose)==='timed_out')lingering=true;
  if(address)await fetch(address.qa_url+'/_qa/stop',{method:'POST',headers:{'x-qa-token':address.stop_token},signal:AbortSignal.timeout(BUDGET.stopRequest)}).catch(()=>{});
  if(serverExit&&await within(serverExit,BUDGET.serverExit)==='timed_out'){
    server.kill();
    if(await within(serverExit,BUDGET.serverKill)==='timed_out')lingering=true;
  }
  server?.stdout.destroy();server?.stderr.destroy();
  await fs.writeFile(path.join(out,'server.log'),logs.join(''));
  if(lingering){console.error(JSON.stringify({status:'failed',cleanup:'owned browser or QA server did not exit within budget'}));process.exit(1);}
}
