// Real UI/HTTP/persistence/CSV with saved fictional observations; no OCR/AI score.
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
const {chromium}=await import(process.env.RYOSHU_PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const out=path.resolve(process.env.RYOSHU_ACCOUNT_OUTPUT||path.join(root,'.local/account-qa',new Date().toISOString().replace(/[:.]/g,'-')));
await fs.mkdir(out,{recursive:true});
const checks=[],errors=[],logs=[];
let server,serverExit,browser,page,address;
async function api(route,body){return page.evaluate(async({route,body})=>{
  const response=await RyoshuSecurity.fetch('/api/'+route,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Ledger-Request':'1'},body:JSON.stringify(body)});
  const result=await response.json();if(!response.ok)throw new Error(JSON.stringify(result));return result;
},{route,body});}
async function saveScreenshot(name){await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});}
async function results(){await page.locator('.nav-item[data-view=results]').click();await page.locator('.results-table').waitFor();}
const row=id=>page.locator(`[data-result-row="${id}"]`);
const dialog=()=>page.locator('#account-change-dialog');
const form=()=>page.locator('#account-change-form');
const category=()=>form().locator('[data-account-category]');
const account=()=>form().locator('select[name=account]');
const submit=()=>form().locator('button[type=submit]');
async function open(id){await row(id).getByRole('button',{name:'科目を変更',exact:true}).click();await dialog().waitFor({state:'visible'});await form().waitFor({state:'visible'});}
async function close(){await page.keyboard.press('Escape');await dialog().waitFor({state:'hidden'});}
async function choose(config,name){const selected=config.accounts.find(a=>a.name===name);assert.ok(selected,name);await category().selectOption(selected.category_id);await account().selectOption(name);return selected;}
try{
  server=spawn(process.env.RYOSHU_QA_PYTHON||'python',[path.join(root,'scripts/serve_browser_qa.py'),'--data-dir',path.join(out,'data')],{cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe']});
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
  page=await browser.newPage({viewport:{width:1365,height:900},acceptDownloads:true});
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(address.open_url,{waitUntil:'networkidle'});
  await page.locator('#file-input').setInputFiles(['V001','V044','V045'].map(id=>path.join(root,'samples/receipts-100-v2/individual',id+'.pdf')));
  await page.locator('#start-import').click();
  await page.waitForFunction(()=>state.records.length===3&&state.records.every(r=>!['queued','processing'].includes(r.status)),null,{timeout:30000});
  await results();
  const config=await api('config'),records=(await api('receipts')).receipts;
  const target=records.find(r=>r.original_name==='V045.pdf'),initial=await api('receipts/'+target.id);
  assert.ok(config.accounts.length>33&&config.account_categories.length>1);
  await open(target.id);
  assert.equal(await account().inputValue(),initial.draft.account);
  assert.equal(await category().inputValue(),config.accounts.find(a=>a.name===initial.draft.account).category_id);
  const originalVersion=initial.version;
  const alternative=config.accounts.find(a=>a.name==='取材費');assert.ok(alternative);
  await category().selectOption(alternative.category_id);
  assert.equal(await account().inputValue(),'');assert.equal(await submit().isEnabled(),false);
  const available=await account().locator('option').evaluateAll(nodes=>nodes.map(node=>node.value).filter(Boolean));
  assert.ok(available.includes('取材費'));
  assert.ok(available.every(name=>config.accounts.find(a=>a.name===name)?.category_id===alternative.category_id));
  await close();
  assert.equal((await api('receipts/'+target.id)).version,originalVersion);
  assert.equal(await row(target.id).getByRole('button',{name:'科目を変更',exact:true}).evaluate(node=>node===document.activeElement),true);
  checks.push('categories filter real account options; category change needs an explicit account; Escape cancels without a save and restores focus');
  await open(target.id);await choose(config,'取材費');await saveScreenshot('01-account-picker');await submit().click();
  await dialog().waitFor({state:'hidden'});
  const changed=await api('receipts/'+target.id);
  assert.deepEqual(changed.draft,{...initial.draft,account:'取材費'});
  assert.equal(changed.status,'review');assert.equal(changed.version,originalVersion+1);
  assert.equal(await row(target.id).locator('.result-account').innerText(),'取材費');
  await page.reload({waitUntil:'networkidle'});await page.locator('.results-table').waitFor();
  await open(target.id);assert.equal(await account().inputValue(),'取材費');assert.equal(await category().inputValue(),alternative.category_id);
  await close();
  checks.push('quick account change persists across reload and changes no other draft field');

  await open(target.id);await choose(config,'事務用品費');
  const beforeConflict=await api('receipts/'+target.id);
  await api('receipts/'+target.id+'/save',{version:beforeConflict.version,draft:{...beforeConflict.draft,description:'別の操作で更新した摘要'},confirm:false});
  await submit().click();
  await page.waitForFunction(()=>document.querySelector('#account-change-dialog')?.innerText.includes('別の操作'));
  assert.equal(await dialog().isVisible(),true);assert.equal(await account().inputValue(),'事務用品費');
  const afterConflict=await api('receipts/'+target.id);
  assert.equal(afterConflict.draft.account,'取材費');assert.equal(afterConflict.draft.description,'別の操作で更新した摘要');
  await close();await page.reload({waitUntil:'networkidle'});
  checks.push('optimistic-lock conflict preserves entered selection and never overwrites a concurrent edit');

  const id=records.find(r=>r.original_name==='V001.pdf').id;
  const candidate=await api('receipts/'+id);
  const reviewed={...candidate.draft,account:'消耗品費',purpose:'架空の業務用事務用品の確認',business_percent:100,payment:'bank',export_tax:'outside'};
  const confirmed=await api('receipts/'+id+'/save',{version:candidate.version,draft:reviewed,confirm:true});
  assert.equal(confirmed.status,'confirmed');assert.ok(confirmed.posting);
  await page.reload({waitUntil:'networkidle'});await open(id);
  assert.match(await dialog().innerText(),/未確認/);
  await choose(config,'事務用品費');await submit().click();await dialog().waitFor({state:'hidden'});
  const unconfirmed=await api('receipts/'+id);
  assert.deepEqual(unconfirmed.draft,{...reviewed,account:'事務用品費'});
  assert.equal(unconfirmed.status,'review');assert.equal(unconfirmed.posting,null);
  const history=await api('receipts/'+id+'/history');
  assert.ok(JSON.stringify(history).includes('消耗品費')&&JSON.stringify(history).includes('事務用品費'));
  checks.push('changing a confirmed account returns it to review, voids its posting and preserves the old/new audit history');

  await row(id).getByRole('button',{name:'確認・修正する',exact:true}).click();
  await page.locator('#review-form').waitFor();
  const review=page.locator('#review-form'),reviewCategory=review.locator('[data-account-category]');
  assert.equal(await review.locator('[name=account]').inputValue(),'事務用品費');
  await reviewCategory.selectOption(alternative.category_id);
  assert.equal(await review.locator('[name=account]').inputValue(),'');
  await review.locator('[name=account]').selectOption('取材費');
  assert.equal(await review.locator('[name=purpose]').inputValue(),reviewed.purpose);
  await results();
  await row(id).getByRole('button',{name:'科目を変更',exact:true}).click();
  assert.equal(await dialog().isVisible(),false);
  assert.match(await page.locator('#toast').innerText(),/保存|未保存|入力/);
  await row(id).getByRole('button',{name:'確認・修正する',exact:true}).click();await review.waitFor();
  assert.equal(await review.locator('[name=account]').inputValue(),'取材費');
  await review.locator('button[type=submit]').click();
  await page.waitForFunction(id=>state.detail?.id===id&&state.detail.status==='confirmed'&&!state.drafts.has(id),id);
  assert.equal((await api('receipts/'+id)).draft.account,'取材費');
  checks.push('detail form uses the same two-level selector and protects unsaved edits from a quick change');

  for(const profile of ['generic','freee','mf','yayoi']){
    const url='export/journal?profile='+profile+'&kind=journal&tax_mode=reviewed';
    const bytes=await page.evaluate(async route=>Array.from(new Uint8Array(await(await RyoshuSecurity.fetch('/api/'+route)).arrayBuffer())),url);
    const data=Buffer.from(bytes);assert.ok(data.length>0);
    const text=new TextDecoder(profile==='yayoi'?'shift-jis':'utf-8').decode(data);
    assert.ok(text.includes('取材費'),profile+' dropped the new account');
    await fs.writeFile(path.join(out,profile+'-new-account.csv'),data);
  }
  checks.push('reconfirmed new account is retained in actual generic/freee/MF/Yayoi CSV responses');

  await results();await page.setViewportSize({width:375,height:812});await open(target.id);
  await choose(config,'法定福利費');
  const layout=await dialog().evaluate(node=>({left:node.getBoundingClientRect().left,right:node.getBoundingClientRect().right,width:innerWidth,scroll:node.scrollWidth,client:node.clientWidth}));
  assert.ok(layout.left>=0&&layout.right<=layout.width&&layout.scroll<=layout.client+1);
  await saveScreenshot('02-account-picker-mobile');await close();
  await page.setViewportSize({width:1365,height:900});
  checks.push('two-level account dialog fits a375px viewport and supports keyboard dismissal');

  await page.locator('.more-nav summary').click();await page.locator('[data-view=catalog]').click();
  await page.locator('#catalog-rows').waitFor();
  const catFilter=page.locator('#catalog-category');
  await catFilter.selectOption(alternative.category_id);
  assert.match(await page.locator('#catalog-rows').innerText(),/取材費/);
  await page.locator('.catalog-add summary').click();
  const add=page.locator('#account-form');
  await add.locator('[name=name]').fill('架空案件調査費');
  await add.locator('[name=category_id]').selectOption(alternative.category_id);
  await add.locator('[name=description]').fill('架空の確認用に追加した調査科目');
  await add.locator('button[type=submit]').click();
  await page.waitForFunction(()=>state.config.accounts.some(a=>a.name==='架空案件調査費'));
  const custom=(await api('config')).accounts.find(a=>a.name==='架空案件調査費');
  assert.equal(custom.category_id,alternative.category_id);
  await results();await open(target.id);await category().selectOption(alternative.category_id);
  assert.ok(await account().locator('option[value="架空案件調査費"]').count());
  await close();await saveScreenshot('03-results-with-new-account');
  checks.push('catalog category filtering and a categorized custom account feed the live two-level picker');
  assert.deepEqual(errors,[]);
}catch(error){errors.push(error.stack||String(error));process.exitCode=1;if(page)await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});}
finally{
  if(page&&address)await page.evaluate(async token=>fetch('/_qa/stop',{method:'POST',headers:{'x-qa-token':token}}),address.stop_token).catch(()=>{});
  if(browser)await browser.close().catch(()=>{});
  if(server){const done=await Promise.race([serverExit,new Promise(resolve=>setTimeout(()=>resolve(null),10000))]);if(!done)server.kill();}
  const sources={};for(const name of ['web/app.js','web/index.html','web/style.css','receipt_ledger/catalog.py','receipt_ledger/store.py','receipt_ledger/server.py','data/account-catalog.json','scripts/account_workflow_smoke.mjs'])sources[name]=createHash('sha256').update(await fs.readFile(path.join(root,name))).digest('hex');
  const result={status:errors.length?'failed':'passed',scope:'Actual browser/HTTP/encrypted persistence using saved fictional observations. New classification inference is separately measured.',checks,errors,source_hashes:sources};
  await fs.writeFile(path.join(out,'verification.json'),JSON.stringify(result,null,2)+'\n');
  if(logs.length)await fs.writeFile(path.join(out,'server-private.log'),logs.join(''));
  console.log(JSON.stringify({status:result.status,checks:checks.length,output:out,errors}));
}
