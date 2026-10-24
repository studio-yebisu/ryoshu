// Real UI/HTTP progression with isolated, saved fictional observations; not new OCR/AI inference.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {spawn,execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {createHash} from 'node:crypto';
const {chromium}=await import(process.env.RYOSHU_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.resolve(process.env.RYOSHU_PROCESSING_OUTPUT||path.join(root,'.local/processing-qa',new Date().toISOString().replace(/[:.]/g,'-')));
const python=process.env.RYOSHU_QA_PYTHON||'python';
const runFile=promisify(execFile),checks=[],errors=[],logs=[];
await fs.mkdir(out,{recursive:true});
let server,serverExit,browser,page,address,shutdown;
const panel=()=>page.locator('#processing-status');
const counter=name=>panel().locator(`[data-processing-count="${name}"]`);
const recent=id=>panel().locator(`[data-processing-recent] [data-processing-id="${id}"]`);
async function api(route,body){return page.evaluate(async({route,body})=>{
  const response=await RyoshuSecurity.fetch('/api/'+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Ledger-Request':'1'},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw new Error(JSON.stringify(result));return result;
},{route,body});}
async function hold(held){return page.evaluate(async({token,held})=>{
  const response=await fetch('/_qa/replay-hold?held='+held,{method:'POST',headers:{'x-qa-token':token}});
  if(!response.ok)throw new Error('QA hold control failed');return response.json();
},{token:address.stop_token,held});}
async function screenshot(name){
  // Chromium's native progress-value transition is not covered by screenshot animation control.
  if(await panel().evaluate(node=>node.classList.contains('is-finished')))await page.waitForTimeout(500);
  await page.screenshot({path:path.join(out,name+'.png'),fullPage:true,animations:'disabled'});
}
async function home(){await page.locator('.nav-item[data-view=home]').click();await page.locator('#choose-files').waitFor();}
async function results(){await page.locator('.nav-item[data-view=results]').click();await page.locator('.results-table').waitFor();}
async function upload(ids){
  await home();
  await page.locator('#file-input').setInputFiles(ids.map(id=>path.join(root,'samples/receipts-100-v2/individual',id+'.pdf')));
  await page.locator('#start-import').click();
}
async function waitCount(name,value){await page.waitForFunction(({name,value})=>Number(document.querySelector(`[data-processing-count="${name}"]`)?.textContent)===value,{name,value},{timeout:30000});}
async function waitFinished(ids){
  await page.waitForFunction(ids=>ids.every(id=>state.records.some(r=>r.id===id&&!['queued','processing'].includes(r.status))),ids,{timeout:60000});
}
async function savedState(id){const r=await api('receipts/'+id);return Object.fromEntries(['status','version','draft','posting','journal'].map(k=>[k,r[k]]));}
async function assertLayout(locator){
  const layout=await locator.evaluate(node=>{const b=node.getBoundingClientRect();return{left:b.left,right:b.right,width:innerWidth,scroll:node.scrollWidth,client:node.clientWidth,documentScroll:document.documentElement.scrollWidth};});
  assert.ok(layout.left>=-1&&layout.right<=layout.width+1&&layout.scroll<=layout.client+1&&layout.documentScroll<=layout.width+1,JSON.stringify(layout));return layout;
}
try{
  const launcher=path.join(root,'scripts/serve_browser_qa.py');
  for(const value of ['-1','3.1','nan','inf']){
    const data=path.join(out,'refused-'+value.replace(/\W/g,'_'));
    try{await runFile(python,[launcher,'--data-dir',data,'--processing-step-delay',value],{cwd:root,windowsHide:true,timeout:10000});assert.fail('Unbounded QA delay accepted');}
    catch(error){assert.equal(error.code,2);assert.match(error.stderr,/0秒から3秒/);}
    await assert.rejects(fs.access(data));
  }
  checks.push('processing replay rejects invalid or unbounded delays before creating any key or database');
  server=spawn(python,[launcher,'--data-dir',path.join(out,'data'),'--processing-step-delay','2.5','--processing-fail-once','V003.pdf'],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
  serverExit=new Promise(resolve=>server.once('exit',(code,signal)=>resolve({code,signal})));
  let pending='';
  address=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('QA server startup timed out')),30000);
    server.once('error',error=>{clearTimeout(timer);reject(error);});
    server.once('exit',code=>{clearTimeout(timer);reject(new Error('QA server exited:'+code));});
    server.stderr.on('data',chunk=>logs.push(chunk.toString()));
    server.stdout.on('data',chunk=>{pending+=chunk.toString();let i;while((i=pending.indexOf('\n'))>=0){const line=pending.slice(0,i);pending=pending.slice(i+1);try{const value=JSON.parse(line);if(value.qa_url){clearTimeout(timer);resolve(value);}}catch{logs.push(line+'\n');}}});
  });
  assert.notEqual(new URL(address.qa_url).port,'8766');
  browser=await chromium.launch({headless:true,...(process.env.RYOSHU_BROWSER_CHANNEL?{channel:process.env.RYOSHU_BROWSER_CHANNEL}:{})});
  page=await browser.newPage({viewport:{width:1365,height:1000}});
  page.on('pageerror',error=>errors.push(error.message));
  page.on('dialog',message=>message.type()==='beforeunload'?message.accept():message.dismiss());
  await page.goto(address.open_url,{waitUntil:'networkidle'});
  const health=await api('health');
  assert.equal(path.resolve(health.data_dir),path.join(out,'data'));assert.equal(path.resolve(health.source_dir),root);
  assert.equal(await panel().isVisible(),false);
  await upload(['V001']);
  await page.waitForFunction(()=>state.records.length===1&&state.records[0].status==='review',null,{timeout:30000});
  const oldId=(await api('receipts')).receipts[0].id,old=await api('receipts/'+oldId);
  await api('receipts/'+oldId+'/save',{version:old.version,draft:{...old.draft,purpose:'処理中UIの確認用に登録した架空の事務用品',business_percent:100,payment:'bank',export_tax:'outside'},confirm:true});
  const oldSaved=await savedState(oldId),oldHistory=await api('receipts/'+oldId+'/history');
  await page.reload({waitUntil:'networkidle'});await page.locator('#choose-files').waitFor();
  assert.equal(await panel().isVisible(),false);
  checks.push('old confirmed history alone does not appear as a new processing batch or inflate progress');

  await hold(true);await upload(['V001','V002','V003','V004']);
  await page.waitForFunction(()=>state.records.length===4,null,{timeout:30000});
  const newRows=(await api('receipts')).receipts.filter(r=>r.id!==oldId),ids=newRows.map(r=>r.id);
  const byName=Object.fromEntries(newRows.map(r=>[r.original_name,r.id]));
  await waitCount('total',3);await waitCount('active',1);await waitCount('waiting',2);await waitCount('completed',0);
  assert.equal(await recent(oldId).count(),0);
  assert.match(await panel().locator('[data-processing-current]').innerText(),/V002\.pdf/);
  assert.match(await panel().locator('[data-processing-stage]').innerText(),/文字|OCR|読み取/);
  assert.equal(await panel().locator('[data-processing-next] [data-state=queued]').count(),2);
  const heldCurrent=await panel().locator('[data-processing-current]').elementHandle();
  await screenshot('01-processing-ocr-waiting');
  checks.push('a four-file upload tracks only the three new receipt IDs, with one real OCR stage and two waiting rows; old and duplicate records are excluded');

  const countsBefore=await panel().locator('[data-processing-count]').evaluateAll(nodes=>nodes.map(n=>[n.dataset.processingCount,n.textContent]));
  await page.route('**/api/receipts',route=>route.request().method()==='GET'?route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'架空の接続断'})}):route.continue());
  await page.waitForFunction(()=>document.querySelector('#processing-status').classList.contains('is-stale'),null,{timeout:15000});
  assert.match(await panel().innerText(),/更新|接続|最後/);
  assert.deepEqual(await panel().locator('[data-processing-count]').evaluateAll(nodes=>nodes.map(n=>[n.dataset.processingCount,n.textContent])),countsBefore);
  await screenshot('02-processing-connection-lost');
  await page.unroute('**/api/receipts');
  await page.waitForFunction(()=>document.querySelector('#processing-status').classList.contains('is-busy')&&!document.querySelector('#processing-status').classList.contains('is-stale'),null,{timeout:15000});
  assert.equal(await heldCurrent.evaluate(node=>node.isConnected),true);
  checks.push('failed progress fetches retain the last real counts and show stale status; reconnecting recovers without replacing the active card');

  await page.locator('#toast').waitFor({state:'hidden',timeout:15000});
  await page.setViewportSize({width:375,height:812});await page.emulateMedia({reducedMotion:'reduce'});
  await assertLayout(panel());
  const animations=await panel().evaluate(node=>[node,...node.querySelectorAll('*')].flatMap(el=>['',':before',':after'].map(p=>getComputedStyle(el,p||null).animationName)).filter(name=>name!=='none'));
  assert.deepEqual(animations,[]);
  await screenshot('03-processing-mobile-reduced-motion');
  await page.setViewportSize({width:1365,height:1000});await page.emulateMedia({reducedMotion:'no-preference'});
  await page.evaluate(()=>{window.__qaProcessingAnimations=[];document.addEventListener('animationstart',event=>{const row=event.target.closest('[data-processing-recent] [data-processing-id]');if(row)window.__qaProcessingAnimations.push({id:row.dataset.processingId,name:event.animationName});},true);});
  checks.push('the active dashboard fits375px and disables CSS motion for the reduced-motion preference');

  await hold(false);
  await page.waitForFunction(()=>/科目|候補/.test(document.querySelector('#processing-status [data-processing-stage]')?.textContent||''),null,{timeout:20000});
  await screenshot('04-processing-model');
  await recent(byName['V002.pdf']).waitFor();
  const completedNode=await recent(byName['V002.pdf']).elementHandle();
  await waitCount('attention',1);
  await waitFinished(ids);await waitCount('processed',3);await waitCount('completed',2);
  assert.equal(await recent(byName['V003.pdf']).getAttribute('data-state'),'failed');
  assert.equal(await panel().locator('[data-action=retry-errors]').isVisible(),true);
  assert.equal(await completedNode.evaluate(node=>node.isConnected),true);
  await screenshot('05-processing-needs-retry');
  const animationsBefore=await page.evaluate(id=>window.__qaProcessingAnimations.filter(a=>a.id===id),byName['V002.pdf']);
  await results();await home();
  assert.equal(await completedNode.evaluate(node=>node.isConnected),true);
  assert.deepEqual(await page.evaluate(id=>window.__qaProcessingAnimations.filter(a=>a.id===id),byName['V002.pdf']),animationsBefore);
  for(const r of (await api('receipts')).receipts.filter(r=>ids.includes(r.id)))assert.ok(['review','error'].includes(r.status));
  for(const id of ids)assert.equal((await api('receipts/'+id)).posting,null);
  assert.deepEqual(await savedState(oldId),oldSaved);assert.deepEqual(await api('receipts/'+oldId+'/history'),oldHistory);
  checks.push('OCR transitions to the model stage and completed checks accumulate; failed work stays distinct, no row is automatically confirmed, and repeated renders do not replay completion animations or replace completed rows');

  await panel().locator('[data-action=retry-errors]').click();
  await waitCount('active',1);await waitCount('attention',0);
  await waitFinished(ids);await waitCount('completed',3);await waitCount('processed',3);
  await page.waitForFunction(()=>document.querySelector('#processing-status').classList.contains('is-finished'));
  assert.equal(await panel().locator('[data-view=results]').isVisible(),true);
  await screenshot('06-processing-finished');
  await results();await home();assert.equal(await panel().isVisible(),true);
  checks.push('retry moves the failed receipt back into real processing, succeeds once, and leaves a persistent finished card and a route to the results');

  await hold(true);await upload(['V005','V006']);
  await page.waitForFunction(()=>state.records.length===6);await waitCount('total',2);await waitCount('completed',0);
  const pendingIds=(await api('receipts')).receipts.filter(r=>['queued','processing'].includes(r.status)).map(r=>r.id);
  assert.equal(pendingIds.length,2);
  await page.reload({waitUntil:'networkidle'});await waitCount('total',2);
  assert.match(await panel().innerText(),/残りの処理/);
  await screenshot('07-processing-resumed-after-reload');
  checks.push('a new upload resets a finished cohort, and reload truthfully recaptures only the two remaining busy receipts rather than past completed history');

  await results();await page.locator(`[data-result-row="${oldId}"]`).getByRole('button',{name:'確認・修正する',exact:true}).click();
  const form=page.locator('#review-form');await form.waitFor();
  const purpose=form.locator('[name=purpose]'),amount=form.locator('[name=total]');
  await amount.fill('12345');await purpose.fill('保存前の架空案件Aの調査費');
  const input=await purpose.elementHandle();
  await page.waitForTimeout(2600);
  assert.equal(await input.evaluate(node=>node.isConnected&&node===document.activeElement),true);
  assert.equal(await purpose.inputValue(),'保存前の架空案件Aの調査費');assert.equal(await amount.inputValue(),'12345');
  assert.deepEqual(await savedState(oldId),oldSaved);
  await hold(false);await waitFinished(pendingIds);await waitCount('completed',2);
  assert.equal(await input.evaluate(node=>node.isConnected&&node===document.activeElement),true);
  assert.equal(await purpose.inputValue(),'保存前の架空案件Aの調査費');assert.equal(await amount.inputValue(),'12345');
  assert.deepEqual(await savedState(oldId),oldSaved);assert.deepEqual(await api('receipts/'+oldId+'/history'),oldHistory);
  checks.push('background stage/timer/completion updates preserve focused unsaved purpose and amount inputs, while saved draft, confirmation, posting and audit remain unchanged');
  await home();await panel().locator('[data-processing-dismiss]').click();assert.equal(await panel().isVisible(),false);
  assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack||String(error));process.exitCode=1;if(page)await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});}
finally{
  if(page&&address)await page.evaluate(async token=>fetch('/_qa/stop',{method:'POST',headers:{'x-qa-token':token}}),address.stop_token).catch(()=>{});
  if(browser)await browser.close().catch(()=>{});
  if(server){let timer;shutdown=await Promise.race([serverExit,new Promise(resolve=>{timer=setTimeout(()=>resolve(null),10000);})]);clearTimeout(timer);if(!shutdown){server.kill();shutdown=await serverExit;}}
  const sources={};for(const name of ['web/app.js','web/import.js','web/processing.js','web/style.css','web/index.html','scripts/serve_browser_qa.py','scripts/processing_workflow_smoke.mjs']){
    try{sources[name]=createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex');}catch{}
  }
  const result={status:errors.length?'failed':'passed',scope:'Actual browser/HTTP progression using isolated saved fictional observations, delayed OCR/model replay and one intentional first-attempt error. Not a new OCR or AI accuracy measurement.',checks,errors,server_exit:shutdown,source_hashes:sources};
  await fs.writeFile(path.join(out,'verification.json'),JSON.stringify(result,null,2)+'\n');
  if(logs.length)await fs.writeFile(path.join(out,'server-private.log'),logs.join(''));
  console.log(JSON.stringify({status:result.status,checks:checks.length,output:out,errors}));
}
