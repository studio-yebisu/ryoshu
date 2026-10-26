'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const yen = value => value == null ? '—' : '¥' + Number(value).toLocaleString('ja-JP');
const localMonth = () => {const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;};
const statusNames = {queued:'順番待ち',processing:'読み取り中',review:'未確認',confirmed:'確認済み',excluded:'対象外',error:'読み取りに失敗'};
const state = {view:'home',config:null,catalog:null,health:null,records:[],active:null,detail:null,sourceTab:'image',page:0,history:null,queueFilter:'pending',queuePage:0,queuePageAnchor:null,month:'',drafts:new Map(),busy:false,ledgerFilter:'all',ledgerSearch:'',ledgerPage:0,selected:new Map(),batch:{},batchReport:null,batchOnlyIssues:false,exportMapDrafts:new Map()};
let toastTimer, pollTimer, searchTimer;
Object.assign(state,{resultsPage:0,resultsOnlyPending:false,reviewAccountCategories:new Map(),catalogCategory:'',catalogSearch:'',favoriteSaving:false});
let receiptRevision=0,detailRevision=0;
Object.assign(state,{exportProfile:'generic',exportKind:'expenses',exportTaxMode:'reviewed',exportRevision:0,exportNewOnly:false,ledgerNeedsRefresh:false});
state.processingFetchedAt=0;
const processingUI=RyoshuProcessing.create($('#processing-status'));

function validationMessage(error){
  if(typeof error==='string')return error;
  const key=error.loc?.slice(-1)[0],label={business_percent:'事業割合',total:'領収書の合計',purpose:'業務での用途',payment:'支払元',date:'取引日',merchant:'支払先',account:'勘定科目',tax_amount:'記載消費税',invoice_number:'登録番号',export_tax:'会計ソフト用の税区分',rows:'選択した明細',version:'更新情報'}[key]||'入力内容';
  const known=String(error.msg||'').replace(/^Value error, /,'');
  let message=/[ぁ-んァ-ン一-龯]/.test(known)?known:'内容を確認してください';
  if(['int_type','int_parsing','int_from_float'].includes(error.type))message='整数で入力してください';
  if(error.type==='greater_than_equal')message=`${error.ctx.ge}以上で入力してください`;
  if(error.type==='less_than_equal')message=`${error.ctx.le}以下で入力してください`;
  if(error.type==='literal_error')message='画面の選択肢から選んでください';
  if(error.type==='missing')message='入力してください';
  if(error.type==='value_error'&&key==='date')message='実在する日付を選んでください';
  return `${label}：${message}`;
}

async function api(path, options = {}) {
  let response;try{response = await RyoshuSecurity.fetch('/api' + path, {...options, headers:{'X-Ledger-Request':'1', ...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'}), ...options.headers}});}catch(error){throw new Error('アプリに接続できません。「Ryoshu.cmd」で起動しているか確認し、もう一度操作してください。',{cause:error});}
  if (!response.ok) {
    let result; try {result = await response.json();} catch {result = {};}
    let message = result.detail || '処理に失敗しました。もう一度お試しください。';
    if (Array.isArray(message)) message = message.map(validationMessage).join('\n');
    throw new Error(message);
  }
  return response.json();
}

function toast(message, error = false) {
  const el = $('#toast'); el.textContent = message; el.classList.toggle('error',error); el.classList.remove('hidden');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.add('hidden'),error ? 9500 : 4500);
}

const badge = status => `<span class="badge ${esc(status)}">${esc(statusNames[status] || status)}</span>`;
const isPending = r => ['queued','processing','review','error'].includes(r.status);
const busyRecord = r => r && ['queued','processing'].includes(r.status);
const currentDraft = () => state.drafts.get(state.active)?.draft || state.detail?.draft;
function summaryFor(month = '') {
  const rows = state.records.filter(r => r.status === 'confirmed' && (r.draft.date||'').startsWith(month));
  return {count:rows.length, total:rows.reduce((v,r)=>v+r.business_amount,0), expenses:rows.filter(r=>r.account_kind!=='asset').reduce((v,r)=>v+r.business_amount,0), assets:rows.filter(r=>r.account_kind==='asset').reduce((v,r)=>v+r.business_amount,0)};
}

function stats(month = '') {
  const sum = summaryFor(month);
  if(!state.config.review_before_ledger){
    const rows=ledgerRows(month),read=rows.filter(r=>!busyRecord(r)),unknown=read.filter(r=>r.draft.total==null).length;
    return `<div class="summary-grid"><div class="stat-card"><div class="stat-label">帳簿の下書き</div><strong>${rows.length}<small>件</small></strong><div class="stat-caption">未確認 ${rows.filter(r=>r.status!=='confirmed').length}件（読み取り中を含む）</div></div><div class="stat-card featured"><div class="stat-label">読み取った領収書の合計</div><strong>${yen(read.reduce((v,r)=>v+(r.draft.total||0),0))}</strong><div class="stat-caption">未確認・私用分・資産を含む${unknown?' / 円額未確定 '+unknown+'件':''}</div></div><div class="stat-card"><div class="stat-label">確認済みの経費</div><strong>${yen(sum.expenses)}</strong><div class="stat-caption">${sum.count}件 / 事業分・税込（資産を除く）</div></div></div>`;
  }
  return `<div class="summary-grid"><div class="stat-card"><div class="stat-label"><span class="stat-dot"></span>確認が必要な領収書</div><strong>${state.records.filter(isPending).length}<small>件</small></strong><div class="stat-caption">読み取り中と再確認が必要な明細を含む</div></div><div class="stat-card featured"><div class="stat-label">確認済みの経費 ${month ? esc(month.replace('-','年')+'月') : '· 全期間'}</div><strong>${yen(sum.expenses)}</strong><div class="stat-caption">事業分・税込（資産を除く）</div><span class="stat-icon">↗</span></div><div class="stat-card"><div class="stat-label">確認済みの領収書</div><strong>${sum.count}<small>件</small></strong><div class="stat-caption">${sum.assets ? 'うち資産 ' + yen(sum.assets) + ' を集計に含む' : '確認操作が済んだ明細を集計'}</div></div></div>`;
}

function renderPendingCount(){
  const count=state.records.filter(isPending).length,el=$('#pending-count');
  el.innerHTML=count?`<span class="visually-hidden">未確認 </span>${count}<span class="visually-hidden">件</span>`:'';
  el.title=`未確認 ${count}件`;
}
function focusPage(top=0,target=null){
  window.scrollTo({top,left:0,behavior:'auto'});
  (target||$('#page-title'))?.focus({preventScroll:true});
}
function returnFromDetail(){
  ++detailRevision;
  const origin=state.detailOrigin||{view:'results',scroll:0};
  state.view=origin.view;
  history.replaceState(null,'','#'+state.view);
  render();
  const trigger=$('[data-open-receipt="'+CSS.escape(state.active||'')+'"]');
  focusPage(origin.scroll,trigger);
}
function render() {
  rememberLedgerInputs();
  document.body.dataset.view=state.view;
  $$('.nav-item').forEach(el => {const active=el.dataset.view===state.view||(state.view==='inbox'&&el.dataset.view==='results');el.classList.toggle('active',active);if(active)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current');});
  renderPendingCount();
  const titles = {home:['領収書を読み取る','PDFや画像から支出の下書きを作ります。'],results:['読み取り結果','原本・読み取った内容・勘定科目の候補を3列で照合します。'],inbox:['領収書の確認','原本を見ながら明細を直し、確認状態を記録します。'],ledger:['CSV出力','形式と期間を選び、内容を確かめて保存します。'],accounts:['科目別の集計','確認済みの支出を科目ごとに集計します。'],settings:['設定・バックアップ','事業の概要、帳簿の保護、復元用の保存を管理します。']};
  titles.catalog=['勘定科目一覧','帳簿の暗号化DBにある科目・用途・確認事項を管理します。'];
  $('#page-title').textContent=titles[state.view][0]; $('#page-description').textContent=titles[state.view][1];
  $('#home-view').classList.toggle('hidden',state.view!=='home');
  $('#upload-top').classList.toggle('hidden',state.view==='home');
  RyoshuImport.setHome(state.view==='home');
  const content = $('#main-content');
  if(state.view==='home'){content.innerHTML='';renderHome();}
  if(state.view==='results')renderResults(content);
  if (state.view==='inbox') renderInbox(content);
  if (state.view==='ledger') renderLedger(content);
  if (state.view==='accounts') renderAccounts(content);
  if (state.view==='settings') renderSettings(content);
  if (state.view==='catalog') renderCatalog(content);
  renderProcessing();
}


function renderHome(){
  const done=state.records.filter(r=>!busyRecord(r)),pending=state.records.filter(isPending).length;
  const html=state.records.length?`<a class="result-link" href="#results" data-view="results">読み取り結果を見る <strong>${done.length} / ${state.records.length}件</strong></a><span class="field-hint">未確認 ${pending}件</span>`:'';
  const resultLink=$('#home-result-link .result-link');
  if(state.records.length&&resultLink){resultLink.querySelector('strong').textContent=`${done.length} / ${state.records.length}件`;$('#home-result-link>.field-hint').textContent=`未確認 ${pending}件`;}
  else if($('#home-result-link').innerHTML!==html)$('#home-result-link').innerHTML=html;
}
function resultAccountButton(row){
  const busy=busyRecord(row),excluded=row.status==='excluded',disabled=busy||excluded;
  const name=row.draft.account||'科目を選ぶ';
  const hint=busy?'読み取り中':excluded?'対象外':'科目を確認';
  const title=busy?'読み取り完了後に科目を選択できます':excluded?'「詳細を修正」で未確認へ戻してください':'勘定科目の候補を確認して選び直す';
  return `<button class="result-account" type="button" data-change-account="${esc(row.id)}" aria-haspopup="dialog" aria-label="${esc('勘定科目を確認・変更：'+(row.draft.account||'未選択'))}" title="${esc(title)}" ${disabled?'disabled':''}><span class="result-account-main"><span class="result-account-name">${esc(name)}</span><svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="m7 4 6 6-6 6"/></svg></span><span class="result-account-hint">${esc(hint)}</span></button>`;
}
function renderResults(content, incremental=false){
  const all=orderedReceipts(state.records.filter(r=>!state.resultsOnlyPending||isPending(r))),pages=Math.max(1,Math.ceil(all.length/100));
  state.resultsPage=Math.max(0,Math.min(state.resultsPage,pages-1));
  const rows=all.slice(state.resultsPage*100,(state.resultsPage+1)*100),pending=state.records.filter(isPending).length;
  const markup=`<div class="results-toolbar"><label class="checkbox-line"><input id="results-pending" type="checkbox" title="読み取り中と再確認が必要な明細も対象です" ${state.resultsOnlyPending?'checked':''}>未確認に絞り込む</label><span class="results-summary">全${state.records.length}件 / 未確認 ${pending}件</span><a class="button primary" href="#ledger" data-view="ledger">CSVに書き出す</a></div>${!all.length?`<section class="results-empty"><p>${state.records.length?'この条件の明細はありません。':'まだ領収書がありません。'}</p><a href="#home" data-view="home">領収書を読み込む</a></section>`:`<div class="results-scroll"><table class="results-table"><thead><tr><th scope="col">原本</th><th scope="col">読み取った内容</th><th scope="col">勘定科目</th></tr></thead><tbody>${rows.map(r=>`<tr data-result-row="${esc(r.id)}" data-result-version="${esc(JSON.stringify([r.version,r.status,r.stage,r.preview_count]))}"><td><button class="result-image" data-zoom-receipt="${esc(r.id)}" ${r.preview_count?'':'disabled'} aria-label="${esc(r.original_name)}の原本を拡大">${r.preview_count?`<img loading="lazy" width="96" height="128" data-private-src="/api/receipts/${r.id}/preview/0" alt="${esc(r.original_name)}の原本">`:`<span>${busyRecord(r)?'読み取り中':'プレビューなし'}</span>`}<span>${r.preview_count?'クリックで拡大':busyRecord(r)?'完了までお待ちください':'詳細から原本を保存'}</span></button><p class="result-filename">${esc(r.original_name)}</p></td><td><div class="result-content"><div class="result-merchant">${esc(r.draft.merchant||'支払先を確認')}</div><div class="result-date">${esc(r.draft.date||'日付を確認')}</div><strong class="result-amount">${yen(r.draft.total)}</strong><p class="result-description">${esc(r.draft.description||'')}</p>${busyRecord(r)?badge(r.status):`<details class="result-ocr"><summary data-ocr-receipt="${esc(r.id)}">読み取った文字を見る</summary><pre class="ocr-text">開くと本文を読み込みます。</pre></details>`}</div></td><td>${resultAccountButton(r)}${badge(r.status)}${r.error?`<p class="review-warning">${esc(r.error)}</p>`:''}<div class="result-account-actions"><button class="button small" data-open-receipt="${esc(r.id)}">詳細を修正</button></div></td></tr>`).join('')}</tbody></table></div><div class="pagination"><button class="button small" data-action="results-prev" ${state.resultsPage?'':'disabled'}>前の100件</button><span tabindex="-1" aria-live="polite">${state.resultsPage*100+1}〜${Math.min((state.resultsPage+1)*100,all.length)}件目 / ${all.length}件</span><button class="button small" data-action="results-next" ${state.resultsPage<pages-1?'':'disabled'}>次の100件</button></div>`}`;
  if(!incremental||!$('.results-table',content)){content.innerHTML=markup;return;}
  const fresh=document.createElement('div');fresh.innerHTML=markup;
  const body=$('.results-table tbody',content),nextBody=$('.results-table tbody',fresh);
  if(!nextBody){
    if($$('[data-result-row]',body).some(row=>row.contains(document.activeElement)||$('.result-ocr[open]',row)||zoomReceipt?.id===row.dataset.resultRow))return;
    content.innerHTML=markup;return;
  }
  const old=new Map($$('[data-result-row]',body).map(row=>[row.dataset.resultRow,row]));
  const nextIds=new Set([...nextBody.children].map(row=>row.dataset.resultRow));
  if([...old.values()].some(row=>!nextIds.has(row.dataset.resultRow)&&(row.contains(document.activeElement)||$('.result-ocr[open]',row)||zoomReceipt?.id===row.dataset.resultRow)))return;
  let position=body.firstElementChild;
  for(const next of [...nextBody.children]){
    const previous=old.get(next.dataset.resultRow);old.delete(next.dataset.resultRow);
    const keep=previous&&(previous.dataset.resultVersion===next.dataset.resultVersion||$('.result-ocr[open]',previous)||previous.contains(document.activeElement)||zoomReceipt?.id===next.dataset.resultRow);
    const row=keep?previous:next;
    if(row!==position)body.insertBefore(row,position);
    if(previous&&!keep)previous.remove();
    position=row.nextElementSibling;
  }
  for(const row of old.values())row.remove();
  const summary=$('.results-summary',content),nextSummary=$('.results-summary',fresh);
  if(summary.textContent!==nextSummary.textContent)summary.textContent=nextSummary.textContent;
  const pager=$('.pagination',content),nextPager=$('.pagination',fresh);
  $('span',pager).textContent=$('span',nextPager).textContent;
  for(const button of $$('button',pager))button.disabled=$(`[data-action="${button.dataset.action}"]`,nextPager).disabled;
}
let zoomReturn,zoomReceipt,zoomPage=0,accountEdit=null;
function paintZoom(){
  const r=zoomReceipt,count=r.preview_count||0,dialog=$('#image-dialog');
  dialog.innerHTML=`<form method="dialog" class="dialog-heading"><h2>${esc(r.original_name)}</h2><button class="button" autofocus>閉じる</button></form><div class="zoom-controls"><button class="button small" data-action="zoom-prev" ${zoomPage?'':'disabled'}>前のページ</button><span>${zoomPage+1} / ${count}</span><button class="button small" data-action="zoom-next" ${zoomPage<count-1?'':'disabled'}>次のページ</button><label>表示倍率 <select id="zoom-scale"><option value="fit">画面に合わせる</option><option value="100">100%</option><option value="150">150%</option><option value="200">200%</option></select></label></div><div class="zoom-image-wrap"><img width="1400" height="1800" data-private-src="/api/receipts/${r.id}/preview/${zoomPage}" alt="${esc(r.original_name)}の原本 ${zoomPage+1}ページ"></div>`;
}
function openZoom(id,trigger){
  const r=state.records.find(r=>r.id===id);if(!r?.preview_count)return;
  zoomReturn=trigger;zoomReceipt=r;zoomPage=0;paintZoom();$('#image-dialog').showModal();
}
function refreshReleasedResult(){queueMicrotask(()=>{if(state.view==='results')renderResults($('#main-content'),true);});}
document.addEventListener('focusout',event=>{if(event.target.closest('.results-table'))refreshReleasedResult();});
document.addEventListener('toggle',event=>{if(event.target.matches('.result-ocr')&&!event.target.open)refreshReleasedResult();},true);
$('#image-dialog').addEventListener('close',()=>{$('#image-dialog').innerHTML='';const target=zoomReturn?.isConnected?zoomReturn:$('#main-content');target.focus({preventScroll:true});zoomReceipt=null;refreshReleasedResult();});

function rememberLedgerInputs(){
  for(const key of ['purpose','percent','payment','tax']){const el=$('#batch-'+key);if(el)state.batch[key]=el.value;}
  if($('#export-account-map'))state.exportMapDrafts.set(state.exportProfile,$('#export-account-map').value);
}

function renderProcessing(){
  processingUI.render(state.records,{importing:RyoshuImport.busy(),paused:RyoshuImport.paused(),fetchedAt:state.processingFetchedAt});
}

function renderInbox(content) {
  if (!state.records.length) {
    content.innerHTML=`<section class="panel empty-state"><h2>確認する明細はまだありません</h2><p>領収書を追加すると、ここで原本と読み取りの候補を照合できます。</p><a class="button primary" href="#home" data-view="home">領収書を取り込む</a></section>`;
    return;
  }
  content.innerHTML=`<div class="workspace"><section class="panel queue"><div class="queue-head"><h2>取り込んだ領収書</h2><span class="table-sub">${state.records.length}件</span></div><select class="queue-filter" aria-label="領収書を絞り込む"><option value="pending">未確認</option><option value="all">すべて</option><option value="confirmed">確認済み</option><option value="excluded">対象外</option></select><div id="queue-pages" class="queue-pages"></div><div class="queue-list" id="queue-list"></div></section><section class="panel review-panel" id="review-panel"></section></div><p class="inbox-note">1ページに1枚の領収書をまとめたPDFは、取り込み時にページ単位へ分割できます。1ページに複数の領収書がある場合は、先に個別の画像やPDFへ分けてください。</p>`;
  $('.queue-filter').value=state.queueFilter;
  renderQueue(); renderDetail();
}

function orderedReceipts(rows){
  const groups=new Map();
  for(const row of rows){const key=row.source_document?.sha256||row.id;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);}
  return [...groups.values()].flatMap(group=>group[0].source_document?group.sort((a,b)=>a.source_document.page-b.source_document.page):group);
}
function nextReviewReceipt(id){
  const ordered=orderedReceipts(state.records),index=ordered.findIndex(r=>r.id===id);
  return [...ordered.slice(index+1),...ordered.slice(0,index)].find(r=>r.status==='review');
}
function visibleRecords() {return orderedReceipts(state.records.filter(r => state.queueFilter==='all' || (state.queueFilter==='pending' ? isPending(r) : r.status===state.queueFilter)));}
function renderQueue() {
  const el = $('#queue-list'); if (!el) return;
  const scroll=el.scrollTop, left=el.scrollLeft,all=visibleRecords(),pages=Math.max(1,Math.ceil(all.length/100));
  if(state.queuePageAnchor!==state.active){const index=all.findIndex(r=>r.id===state.active);if(index>=0)state.queuePage=Math.floor(index/100);state.queuePageAnchor=state.active;}
  state.queuePage=Math.max(0,Math.min(state.queuePage,pages-1));
  if($('#queue-pages'))$('#queue-pages').innerHTML=pages>1?`<button class="text-button" data-action="queue-prev" ${state.queuePage?'':'disabled'}>前へ</button><span>${state.queuePage+1} / ${pages}ページ</span><button class="text-button" data-action="queue-next" ${state.queuePage<pages-1?'':'disabled'}>次へ</button>`:'';
  el.innerHTML=all.slice(state.queuePage*100,(state.queuePage+1)*100).map(r=>`<button class="queue-item ${state.active===r.id?'selected':''}" data-receipt="${esc(r.id)}"><${r.preview_count?'img':'span'} loading="lazy" class="receipt-thumb ${r.preview_count?'':'thumb-placeholder'}" ${r.preview_count?`data-private-src="/api/receipts/${r.id}/preview/0" alt=""`:'aria-hidden="true"'}>${r.preview_count?'':'▤</span>'}<div class="queue-item-info"><div class="queue-item-name">${esc(r.draft.merchant || r.original_name)}</div><div class="queue-item-meta">${esc(r.draft.date || '日付を確認')}</div><div class="queue-item-amount">${yen(r.draft.total)}</div><div class="queue-badge">${badge(r.status)}${state.drafts.has(r.id)?'<span class="unsaved"> · 未保存</span>':''}</div></div></button>`).join('') || '<div class="empty-queue">この条件の領収書はありません</div>';
  el.scrollTop=scroll;el.scrollLeft=left;
}

function field(name,label,type='text', options={}) {
  const d=currentDraft()||{}, value=d[name]??'';
  let input;
  if (type==='select') input=`<select name="${name}" ${options.required?'required':''}>${options.items.map(([v,l])=>`<option value="${esc(v)}" ${String(value)===String(v)?'selected':''}>${esc(l)}</option>`).join('')}</select>`;
  else if (type==='textarea') input=`<textarea name="${name}" placeholder="${esc(options.placeholder||'')}" ${options.required?'required':''} maxlength="1000">${esc(value)}</textarea>`;
  else input=`<input name="${name}" type="${type}" value="${esc(value)}" placeholder="${esc(options.placeholder||'')}" ${options.required?'required':''} ${type==='number'?`min="0" max="${name==='business_percent'?100:999999999}" step="1" inputmode="numeric"`:''} ${type==='number'?'class="money-input"':''} ${options.maxlength?`maxlength="${options.maxlength}"`:''}>`;
  return `<label class="field"><span class="field-label">${esc(label)}${options.required?'<span class="required">*</span>':''}</span>${input}${options.hint?`<div class="field-hint">${esc(options.hint)}</div>`:''}</label>`;
}

// Categories are presentation data; only the chosen account enters a receipt.
function selectableAccounts(){
  return (state.config?.accounts||[]).filter(a=>a.selectable!==false&&(!a.kind||['expense','asset'].includes(a.kind)));
}
function accountDefinitions(){
  const supplied=state.config?.account_categories||state.catalog?.account_categories||[];
  const seen=new Set(),rows=[];
  for(const row of supplied)if(row.id&&row.label&&!seen.has(row.id)){rows.push(row);seen.add(row.id);}
  for(const row of [{id:'__expense',label:'その他の経費',kinds:['expense']},{id:'__asset',label:'資産',kinds:['asset']},{id:'__other',label:'支払元・事業主勘定',kinds:['cash','bank','liability','equity']}])if(!seen.has(row.id))rows.push(row);
  return rows;
}
function accountCategoryId(account){
  if(account?.category_id&&accountDefinitions().some(c=>c.id===account.category_id))return account.category_id;
  return account?.kind==='asset'?'__asset':!account?.kind||account.kind==='expense'?'__expense':'__other';
}
function findAccount(value){
  const key=String(value||'').normalize('NFKC').replace(/\s+/g,'').toLocaleLowerCase();
  if(!key)return undefined;
  const accounts=selectableAccounts(),matches=v=>String(v||'').normalize('NFKC').replace(/\s+/g,'').toLocaleLowerCase()===key;
  return accounts.find(a=>matches(a.name))||accounts.find(a=>matches(a.code))||accounts.find(a=>(a.aliases||[]).some(matches));
}
function accountCategories(accounts=selectableAccounts()){
  const used=new Set(accounts.map(accountCategoryId));
  return accountDefinitions().filter(c=>used.has(c.id));
}
function accountCategoryOptions(accounts,value='',emptyLabel='カテゴリーを選んでください'){
  return '<option value="">'+esc(emptyLabel)+'</option>'+accountCategories(accounts).map(c=>'<option value="'+esc(c.id)+'"'+(c.id===value?' selected':'')+'>'+esc(c.label)+'</option>').join('');
}
function accountOptions(category,value=''){
  const rows=selectableAccounts().filter(a=>accountCategoryId(a)===category);
  return '<option value="">'+(category?'科目を選んでください':'カテゴリーを先に選んでください')+'</option>'+rows.map(a=>'<option value="'+esc(a.name)+'"'+(a.name===value?' selected':'')+'>'+esc(a.name)+'</option>').join('');
}
function accountHelp(account){
  if(!account)return 'カテゴリーを選ぶと、その中の科目を選べます。';
  return (account.description?'<p>'+esc(account.description)+'</p>':'')+(account.review_questions?.length?'<p class="field-hint">確認すること：'+account.review_questions.map(esc).join(' / ')+'</p>':'');
}
function favoriteAccounts(){
  const codes=new Set(state.config?.favorite_accounts||[]);
  return selectableAccounts().filter(a=>codes.has(a.code));
}
function isFavoriteAccount(account){return !!account&&(state.config?.favorite_accounts||[]).includes(account.code);}
function favoriteAccountButton(account){
  const selected=isFavoriteAccount(account),label=account?account.name+'をよく使う科目'+(selected?'から外す':'に追加'):'科目を選ぶとスターを付けられます';
  return '<button type="button" class="account-star'+(selected?' starred':'')+'" data-favorite-toggle="'+esc(account?.code||'')+'" aria-pressed="'+selected+'" aria-disabled="'+state.favoriteSaving+'" aria-label="'+esc(label)+'" title="'+esc(label)+'"'+(!account?' disabled':'')+'><span aria-hidden="true">'+(selected?'★':'☆')+'</span></button>';
}
function favoriteAccountChoices(){
  const accounts=favoriteAccounts();
  return accounts.length?'<span class="field-label">よく使う科目</span><div class="account-favorite-list">'+accounts.map(a=>'<button type="button" class="account-favorite-choice" data-favorite-choice="'+esc(a.code)+'"><span aria-hidden="true">★ </span>'+esc(a.name)+'</button>').join('')+'</div>':'<p class="field-hint">☆を押すと、よく使う科目をここに並べられます。</p>';
}
function catalogFavoriteButton(account){
  return account.selectable&&['expense','asset'].includes(account.kind)?favoriteAccountButton(account).replace('data-favorite-toggle=','data-catalog-favorite data-favorite-toggle='):'';
}
function paintFavoriteButton(button,account){
  const template=document.createElement('template');template.innerHTML=favoriteAccountButton(account);
  const fresh=template.content.firstElementChild;
  for(const name of ['class','data-favorite-toggle','aria-pressed','aria-disabled','aria-label','title'])button.setAttribute(name,fresh.getAttribute(name));
  button.disabled=fresh.disabled;button.innerHTML=fresh.innerHTML;
}
function refreshAccountFavorites(){
  for(const picker of $$('[data-account-picker]')){
    const slot=$('[data-account-favorites]',picker),focused=slot.contains(document.activeElement)?document.activeElement.dataset.favoriteChoice:null;
    const markup=favoriteAccountChoices();if(slot.innerHTML!==markup)slot.innerHTML=markup;
    if(focused)slot.querySelector('[data-favorite-choice="'+CSS.escape(focused)+'"]')?.focus({preventScroll:true});
    paintFavoriteButton($('[data-account-star] button',picker),findAccount($('[data-account-choice]',picker).value));
  }
  for(const button of $$('[data-catalog-favorite]')){
    const account=selectableAccounts().find(a=>a.code===button.dataset.favoriteToggle);
    paintFavoriteButton(button,account);
  }
}
async function toggleAccountFavorite(code){
  if(state.favoriteSaving||state.busy||accountEdit?.saving||accountEdit?.reloading)return;
  const account=selectableAccounts().find(a=>a.code===code);if(!account)return;
  const favorite=!isFavoriteAccount(account);
  state.favoriteSaving=true;refreshAccountFavorites();
  try{
    const result=await api('/accounts/'+encodeURIComponent(code)+'/favorite',{method:'POST',body:JSON.stringify({favorite})});
    state.config.favorite_accounts=result.favorite_accounts;
    toast(favorite?account.name+'をよく使う科目に追加しました。':account.name+'をよく使う科目から外しました。');
  }catch(error){toast('よく使う科目を保存できませんでした。'+error.message,true);}
  finally{state.favoriteSaving=false;refreshAccountFavorites();}
}
function chooseFavoriteAccount(picker,code){
  const account=favoriteAccounts().find(a=>a.code===code);if(!account)return;
  const category=$('[data-account-category]',picker),choice=$('[data-account-choice]',picker);
  category.value=accountCategoryId(account);changeAccountCategory(picker);
  choice.value=account.name;
  choice.dispatchEvent(new Event('change',{bubbles:true}));
  choice.focus();
}
function accountPicker(value='',category='',id='review-account'){
  const account=findAccount(value);
  category=account?accountCategoryId(account):category;
  return '<div class="account-picker" data-account-picker><div class="account-favorites" data-account-favorites>'+favoriteAccountChoices()+'</div><div class="form-row"><label class="field"><span class="field-label">カテゴリー<span class="required">*</span></span><select id="'+esc(id)+'-category" data-account-category required>'+accountCategoryOptions(selectableAccounts(),category)+'</select></label><div class="field"><label class="field-label" for="'+esc(id)+'">勘定科目<span class="required">*</span></label><div class="account-choice-row"><select id="'+esc(id)+'" name="account" data-account-choice required'+(category?'':' disabled')+'>'+accountOptions(category,account?.name||'')+'</select><span data-account-star>'+favoriteAccountButton(account)+'</span></div></div></div><div class="account-picker-help" data-account-help aria-live="polite">'+accountHelp(account)+'</div></div>';
}
function changeAccountCategory(picker){
  const category=$('[data-account-category]',picker).value,choice=$('[data-account-choice]',picker);
  choice.innerHTML=accountOptions(category);
  choice.disabled=!category;
  $('[data-account-help]',picker).innerHTML=accountHelp(null);
  $('[data-account-star]',picker).innerHTML=favoriteAccountButton(null);
}
function updateAccountHelp(picker){
  const account=findAccount($('[data-account-choice]',picker).value);
  $('[data-account-help]',picker).innerHTML=accountHelp(account);
  $('[data-account-star]',picker).innerHTML=favoriteAccountButton(account);
}
function addAccountCategoryOptions(kind,selected=''){
  return '<option value="">カテゴリーを選択（任意）</option>'+accountDefinitions().filter(c=>!c.id.startsWith('__')&&(!c.kinds||c.kinds.includes(kind))).map(c=>'<option value="'+esc(c.id)+'"'+(c.id===selected?' selected':'')+'>'+esc(c.label)+'</option>').join('');
}
function accountChangeHeading(){
  return '<div class="dialog-heading"><h2 id="account-change-title">勘定科目を変更</h2><button class="button small" type="button" data-action="cancel-account-change">閉じる</button></div>';
}
function accountChangeNotice(row){
  return row.status==='confirmed'?'科目を保存すると未確認へ戻ります。変更後の内容を再確認するまで、確認済みだけを出力するCSVには入りません。':'科目だけを変更して保存します。金額・日付・用途などの入力は変えません。';
}
function showAccountChangeError(message){
  const box=$('#account-change-error');if(!box)return;
  box.textContent=message;box.classList.remove('hidden');
  $('#account-change-reload')?.classList.remove('hidden');
}
function paintAccountChange(){
  const edit=accountEdit,dialog=$('#account-change-dialog'),row=edit.row;
  dialog.innerHTML=accountChangeHeading()+'<p class="account-change-source">'+esc(row.draft.merchant||row.original_name)+'<span>'+esc(row.original_name)+' · '+yen(row.draft.total)+'</span></p><p class="account-change-notice">'+esc(accountChangeNotice(row))+'</p><form id="account-change-form" novalidate><fieldset class="account-change-fields">'+accountPicker(row.draft.account,'','quick-account')+'</fieldset><div id="account-change-error" class="error-box hidden" role="alert"></div><button id="account-change-reload" class="text-button hidden" type="button" data-action="reload-account-change">最新の内容を読み直す</button><div class="account-change-actions"><button class="button" type="button" data-action="cancel-account-change">キャンセル</button><button class="button primary" type="submit">変更を保存</button></div></form>';
  $('#quick-account-category').focus();
  updateAccountChangeSave();
}
function updateAccountChangeSave(){
  const button=$('#account-change-form button[type="submit"]');
  if(button)button.disabled=!!(accountEdit?.saving||accountEdit?.reloading||accountEdit?.unavailable)||!findAccount($('#quick-account')?.value);
}
async function openAccountChange(id,trigger){
  if(accountEdit||state.busy)return;
  if(state.drafts.has(id)){toast('未保存の入力があります。先に「詳細を修正」で保存してから科目を選び直してください。',true);return;}
  const edit={id,trigger,row:null,saving:false},dialog=$('#account-change-dialog');accountEdit=edit;
  dialog.innerHTML=accountChangeHeading()+'<p role="status">最新の明細を読み込んでいます…</p>';dialog.showModal();
  try{
    const row=await api('/receipts/'+id);if(accountEdit!==edit||!dialog.open)return;
    if(busyRecord(row))throw new Error('読み取りが終わるまで科目は変更できません。完了後にもう一度開いてください。');
    if(row.status==='excluded')throw new Error('対象外の明細の科目は変更できません。「詳細を修正」で未確認へ戻してください。');
    edit.row=row;paintAccountChange();
  }catch(error){if(accountEdit===edit){dialog.close();toast(error.message,true);}}
}
async function reloadAccountChange(){
  const edit=accountEdit;if(!edit?.row||edit.saving||edit.reloading)return;
  const button=$('#account-change-reload');button.disabled=true;edit.reloading=true;
  $('.account-change-fields').disabled=true;updateAccountChangeSave();
  try{
    const row=await api('/receipts/'+edit.id);if(accountEdit!==edit)return;
    if(busyRecord(row)||row.status==='excluded'){edit.unavailable=true;throw new Error('現在は科目を変更できません。閉じて明細の状態を確認してください。');}
    edit.unavailable=false;edit.row=row;
    $('.account-change-notice').textContent=accountChangeNotice(row);
    $('.account-change-source').innerHTML=esc(row.draft.merchant||row.original_name)+'<span>'+esc(row.original_name)+' · '+yen(row.draft.total)+'</span>';
    $('#account-change-error').textContent='別の操作による変更を読み込みました。選択中の科目を確かめてから保存してください。';
    button.classList.add('hidden');
  }catch(error){if(accountEdit===edit)showAccountChangeError(error.message);}
  finally{edit.reloading=false;if(accountEdit===edit){button.disabled=false;$('.account-change-fields').disabled=false;updateAccountChangeSave();}}
}
async function saveAccountChange(){
  const edit=accountEdit;if(!edit?.row||edit.saving||edit.reloading||edit.unavailable||state.busy)return;
  const choice=$('#quick-account'),account=findAccount(choice.value);
  if(!account){showAccountChangeError('カテゴリーを選び、続いて勘定科目を選択してください。');choice.disabled?$('#quick-account-category').focus():choice.focus();return;}
  if(account.name===(findAccount(edit.row.draft.account)?.name||edit.row.draft.account)){$('#account-change-dialog').close();return;}
  edit.saving=true;state.busy=true;
  const dialog=$('#account-change-dialog');$$('button',dialog).forEach(b=>b.disabled=true);$('.account-change-fields',dialog).disabled=true;
  let saved=false,refreshFailed=false;
  try{
    const result=await api('/receipts/'+edit.id+'/save',{method:'POST',body:JSON.stringify({version:edit.row.version,draft:{...edit.row.draft,account:account.name},confirm:false,remember_account:false})});
    saved=true;
    state.records=state.records.map(r=>r.id===result.id?result:r);state.selected.delete(result.id);
    if(state.detail?.id===result.id){state.detail=result;state.history=null;}
    try{await loadReceipts();}catch{refreshFailed=true;}
    render();dialog.close();toast(refreshFailed?'科目は保存されましたが、最新の一覧を取得できませんでした。再読み込みして確認してください。':'科目を保存しました。この明細は未確認です。',refreshFailed);
  }catch(error){if(accountEdit===edit)showAccountChangeError(error.message);}
  finally{
    edit.saving=false;state.busy=false;
    if(!saved&&accountEdit===edit){$$('button',dialog).forEach(b=>b.disabled=false);$('.account-change-fields',dialog).disabled=false;updateAccountChangeSave();}
  }
}
$('#account-change-dialog').addEventListener('cancel',event=>{if(accountEdit?.saving)event.preventDefault();});
$('#account-change-dialog').addEventListener('close',()=>{
  const edit=accountEdit;accountEdit=null;
  $('#account-change-dialog').innerHTML='';
  queueMicrotask(()=>{const target=edit?.trigger?.isConnected?edit.trigger:$$('[data-change-account]').find(el=>el.dataset.changeAccount===edit?.id)||$('#main-content');target?.focus({preventScroll:true});});
});

function renderDetail() {
  const el=$('#review-panel'); if(!el)return;
  const r=state.detail;
  if(!r || r.id!==state.active){el.innerHTML='<div class="preview-empty">左の領収書を選んで、内容を確認できます。</div>';return;}
  const d=currentDraft(),s=r.suggestion;
  const name=r.draft.merchant || r.original_name;
  const top=`<div class="review-top"><div><button class="text-button" type="button" data-action="back-from-detail">${esc(({home:'読み込む画面',ledger:'CSV出力',accounts:'科目別の集計'})[state.detailOrigin?.view]||'読み取り結果')}に戻る</button><h2>${esc(name)}</h2><div class="review-subtitle">${esc(r.original_name)} · ${esc(r.id.slice(0,8).toUpperCase())}</div></div>${badge(r.status)}</div>`;
  if(busyRecord(r)) {el.innerHTML=top+`<div class="processing-box"><span class="spinner"></span><h3>${r.stage==='model'?'購入内容と勘定科目の候補を作成中':'領収書の文字を読み取り中'}</h3><p>${r.stage==='model'?'ローカルPC上のAIが日付・金額・科目の候補を作っています。':'保存した原本を順に処理しています。このままお待ちください。'}<br>処理後に候補がこの画面へ表示されます。</p></div>`;return;}
  el.innerHTML=top+`<div class="review-body"><div class="source-panel"><div class="source-tabs"><button class="source-tab ${state.sourceTab==='image'?'active':''}" data-source-tab="image">原本</button><button class="source-tab ${state.sourceTab==='ocr'?'active':''}" data-source-tab="ocr">OCR本文</button><button class="source-tab ${state.sourceTab==='history'?'active':''}" data-source-tab="history">変更履歴</button></div><div id="source-content"></div></div><div class="form-panel">${r.possible_duplicate_ids?.length?`<div class="warning-list" id="detail-duplicates"><p>日付・支払先・金額が同じ明細があります。別の支出か、二重取り込みかを原本で確認してください。自動では削除しません。</p><p>関連する明細（最大10件）：${r.possible_duplicate_ids.map(id=>`<button type="button" class="text-button" data-open-receipt="${esc(id)}">${esc(state.records.find(other=>other.id===id)?.original_name||'関連明細')} · ${esc(id.slice(0,8).toUpperCase())}</button>`).join(' / ')}</p></div>`:''}${r.status==='excluded'?'<div class="warning-list">この領収書は集計の対象外です。保存すると未確認に戻ります。</div>':''}${r.error?`<div class="error-box">${esc(r.error)}</div>`:''}${s?`<div class="suggestion-card"><div class="suggestion-label"><span>${s.origin==='merchant_rule'?'支払先ルールの候補':s.model?(s.retrieval?.length?'品目辞書・AIの候補':'AIによる科目候補'):'手入力で確認'}</span><span>${s.model?(s.account?'AIの自己評価（参考）：'+({high:'高',medium:'中',low:'低'}[s.raw_model_candidate?.confidence||s.confidence]||'低'):'要確認'):''}</span></div><h3>${esc(s.account||'用途・科目を確認してください')}</h3><p>${esc(s.reason)}</p>${s.review_questions?.length?`<details class="candidate-questions"><summary>この候補の確認ポイント</summary><p>${s.review_questions.map(esc).join(' / ')}</p></details>`:''}${s.model?'<button class="text-button" data-action="use-suggestion">AI候補を入力欄に反映する</button>':''}</div>`:''}${s?.warnings?.length?`<div class="warning-list">${s.warnings.map(w=>`<p>· ${esc(w)}</p>`).join('')}</div>`:''}<form id="review-form" novalidate><p class="field-hint required-note">＊の必須項目をそろえてから確認済みにしてください。確認状態を変えずに内容だけを保存することもできます。</p><div class="form-row">${field('date','取引日','date',{required:true})}${field('total','領収書の合計（税込・円）','number',{required:true,placeholder:'原本の合計金額'})}</div>${field('merchant','支払先','text',{required:true,placeholder:'店舗名・取引先名',maxlength:200})}${accountPicker(d.account,state.reviewAccountCategories.get(r.id)||'')}${field('description','購入内容・摘要','text',{placeholder:'例：制作資料の書籍',maxlength:1000})}${field('purpose','業務での用途','textarea',{required:true,placeholder:'例：案件Aの調査資料として使用'})}<div class="form-row">${field('business_percent','事業割合（%）','number',{required:true,placeholder:'例：100',hint:'仕事専用なら100%。私用と共用なら、使用時間や面積などに基づく事業割合を入力'})}${field('payment','支払元','select',{required:true,items:[['','支払元を選択'],...Object.entries(state.config.payments).map(([k,v])=>[k,v.label])]})}</div><div class="business-result"><span>帳簿に入れる事業分</span><strong id="business-amount">${d.total!=null&&d.business_percent!=null?yen(Math.round(d.total*d.business_percent/100)):'—'}</strong></div><details class="tax-details"><summary>税区分・税額・登録番号（会計ソフトへの出力前に確認）</summary>${field('tax_rate','記載されている税率','select',{items:Object.entries(state.config.tax_rates)})}${field('tax_amount','記載消費税（円）','number',{placeholder:'書かれている場合のみ'})}${field('invoice_number','記載登録番号','text',{placeholder:'T と13桁の数字',maxlength:14,hint:'番号の記録のみ。登録事業者の照会は行いません。'})}</details><label class="checkbox-line"><input name="remember_account" type="checkbox" ${state.drafts.get(r.id)?.remember_account?'checked':''}>この支払先の科目を次回の候補にする</label><div id="form-errors" class="error-box hidden" role="alert"></div><p class="field-hint">金額・科目・用途を原本と照合し、ご自身で確認してください。税務上の最終判断は利用者と税理士が行います。</p><div class="form-actions"><button class="button" type="button" data-action="save-draft">変更を保存（未確認のまま）</button><button class="button primary" type="submit" ${s?.blocking_reasons?.length?'disabled title="この取引は別途の仕訳確認が必要です"':''}>${r.status==='confirmed'?'修正を確認済みにする':'この明細を確認済みにする'}</button></div><div id="save-status" class="${state.drafts.has(r.id)?'unsaved':'field-hint'}">${state.drafts.has(r.id)?'変更はまだ保存していません。':r.status==='confirmed'?'登録済み。下書き保存すると未確認に戻ります。':'明細は保存済みです。原本・勘定科目・用途を確認し、問題なければ確認済みにできます。'}</div><div class="journal-preview" id="journal-preview"></div><div class="secondary-actions"><button class="text-button" type="button" data-action="confirm-next" ${s?.blocking_reasons?.length?'disabled title="この取引は個別の仕訳確認が必要です"':''}>確認済みにして次へ</button><button class="text-button" type="button" data-action="retry">もう一度読み取る</button><button class="text-button" type="button" data-action="${r.status==='excluded'?'restore':'exclude'}">${r.status==='excluded'?'未確認に戻す':'経費の対象外にする'}</button></div></form></div></div>`;
  $('.tax-details',el)?.insertAdjacentHTML('beforeend',field('export_tax','会計ソフトへ出す消費税区分','select',{items:Object.entries(state.config.export_tax_choices||{'':'未確認'}),hint:'会計ソフト向けCSVへ出す区分です。印字税率やT番号だけからは選びません。申告に使う税額は取り込み先で計算してください。'}));
  if(s?.blocking_reasons?.length)$('#review-form .form-actions',el).insertAdjacentHTML('beforebegin',`<div class="error-box"><strong>この明細は確認済みにできません</strong>${s.blocking_reasons.map(reason=>`<p>${esc(reason)}</p>`).join('')}<p>表示された理由を原本と取引内容で解消してください。支出を集計から外すときは「経費の対象外にする」を選びます。</p></div>`);
  renderSource();renderJournal(d);
}

function renderJournal(d) {
  const el=$('#journal-preview');if(!el)return;
  if(!d.payment||!d.account||d.total==null||d.business_percent==null){el.classList.add('hidden');return;}
  const business=Math.round(d.total*d.business_percent/100),privatePart=d.total-business,credit=state.config.payments[d.payment].credit;
  el.classList.remove('hidden');el.innerHTML=`<div class="field-label">登録する仕訳のプレビュー（税込）</div><div><span>${esc(d.account)} / ${esc(credit)}</span><strong>${yen(business)}</strong></div>${d.payment!=='owner'&&privatePart?`<div><span>事業主貸 / ${esc(credit)}</span><strong>${yen(privatePart)}</strong></div>`:''}`;
}

function renderSource() {
  const el=$('#source-content');if(!el||!state.detail)return;
  const r=state.detail;
  if(state.sourceTab==='image') {
    const count=r.preview_count||0;state.page=Math.max(0,Math.min(state.page,count-1));
    el.innerHTML=`<div class="preview-toolbar"><span class="page-pager"><button data-action="prev-page" aria-label="前のページ" ${state.page===0?'disabled':''}>‹</button>${count?state.page+1:0} / ${count}<button data-action="next-page" aria-label="次のページ" ${state.page>=count-1?'disabled':''}>›</button></span><a href="/api/receipts/${r.id}/original">原本を保存</a></div>${(r.source_documents||[r.source_document]).filter(Boolean).map(source=>`<p class="source-caption">${esc(source.name)} の ${source.page} / ${source.page_count}ページを取り込み · <a href="/api/receipts/${r.id}/source?source=${source.sha256}">分割前の元PDFを保存</a></p>`).join('')}${count?`<div class="preview-wrap"><img data-private-src="/api/receipts/${r.id}/preview/${state.page}" alt="取り込んだ領収書の原本プレビュー"></div>`:'<div class="preview-empty">プレビューを作成できませんでした。<br>原本ファイルを開いて確認できます。</div>'}<div class="source-caption">${r.ocr?`Indra OCR ${esc(r.ocr.version)} · ${r.ocr.page_count}ページ · OCR ${r.ocr.seconds??'未計測'}秒${r.suggestion?.seconds?' / AI '+r.suggestion.seconds+'秒':''}<br>`:''}原本のSHA-256：${esc(r.sha256.slice(0,16))}…</div>`;
  }else if(state.sourceTab==='ocr') {
    const evidence=r.suggestion?.evidence||{};
    el.innerHTML=`${Object.keys(evidence).length?`<div class="source-caption">候補に使われた原本の記載</div><div class="journal-preview">${Object.entries(evidence).map(([k,v])=>`<p><strong>${esc({date:'日付',merchant:'支払先',total:'金額'}[k])}</strong>：${esc(v)}</p>`).join('')}</div>`:''}<pre class="ocr-text">${esc(r.ocr?.text||'OCR本文はありません。原本を見ながら入力できます。')}</pre><div class="source-caption">読み違いがある場合は、原本を確認して入力欄を修正してください。</div>`;
  }else {
    const names={import:'原本を取り込み',analysis:'OCR・候補を取得',policy_refresh:'確認条件の更新',save:'下書きを保存',confirm:'確認済みに変更',exclude:'対象外に変更',restore:'未確認に戻す',retry:'再読み取りを開始'};
    el.innerHTML=state.history ? state.history.map(h=>`<div class="history-item"><time>${esc(new Date(h.at).toLocaleString('ja-JP'))}</time><strong>${esc(names[h.action]||h.action)}</strong>${h.detail?.after?`<details><summary>保存した内容</summary><pre>${esc(JSON.stringify(h.detail.after,null,2))}</pre></details>`:''}</div>`).join(''):'<div class="preview-empty">履歴を読み込み中…</div>';
  }
}

function monthControl() {return `<div class="toolbar-group"><input class="filter-input" id="month-filter" type="month" value="${esc(state.month)}" aria-label="${state.view==='ledger'?'対象の期間（CSVと一覧）':'集計する月'}"><button class="button small" data-action="all-months" aria-pressed="${!state.month}">全期間</button></div>`;}
function ledgerRows(month=state.month){
  const states=state.config.review_before_ledger?['confirmed']:['queued','processing','review','error','confirmed'];
  return state.records.filter(r=>states.includes(r.status)&&(!month||!r.draft.date||r.draft.date.startsWith(month))).sort((a,b)=>(a.draft.date||'9999').localeCompare(b.draft.date||'9999'));
}
function importCard(){
  return `<section class="panel import-launch"><div><h2>領収書をまとめて取り込む</h2><p>領収書の画像・PDF・フォルダーを追加できます。PDFは1ファイルを1取引にするか、1ページを1領収書にするかを選べます。</p></div><button class="button" data-action="upload">ファイル・フォルダーを選ぶ</button></section>`;
}

function ledgerSelection(){
  const issueIds=new Set(batchIssues().map(issue=>issue.id));
  if(state.batchOnlyIssues&&!issueIds.size)state.batchOnlyIssues=false;
  const all=state.batchOnlyIssues?state.records.filter(row=>issueIds.has(row.id)):state.ledgerFilter==='excluded'?state.records.filter(r=>r.status==='excluded'&&(!state.month||!r.draft.date||r.draft.date.startsWith(state.month))):ledgerRows(),query=state.ledgerSearch.normalize('NFKC').toLocaleLowerCase();
  const filtered=all.filter(r=>(state.ledgerFilter==='all'||state.ledgerFilter==='excluded'||(state.ledgerFilter==='review'?r.status!=='confirmed':state.ledgerFilter==='error'?!!r.error||r.status==='error':r.status==='confirmed'))&&(!query||[r.original_name,r.draft.merchant,r.draft.description,r.draft.account,r.draft.date,r.draft.purpose].join(' ').normalize('NFKC').toLocaleLowerCase().includes(query)));
  const pages=Math.max(1,Math.ceil(filtered.length/100));state.ledgerPage=Math.min(state.ledgerPage,pages-1);
  const rows=filtered.slice(state.ledgerPage*100,(state.ledgerPage+1)*100),selectable=rows.filter(r=>!busyRecord(r)&&r.status!=='confirmed');
  return {all,filtered,pages,rows,selectable};
}

function ledgerBody(rows,all){return rows.map(r=>`<tr><td>${!busyRecord(r)&&r.status!=='confirmed'?`<input type="checkbox" data-ledger-select="${esc(r.id)}" ${state.selected.has(r.id)?'checked':''} aria-label="${esc(r.draft.merchant||r.original_name)}を選択">`:''}</td><td>${badge(r.status)}${r.error?'<span class="table-sub">読取エラーあり</span>':''}</td><td>${esc(r.draft.date||'未確定')}</td><td><div class="table-main">${esc(r.draft.merchant||r.original_name)}</div><div class="table-sub">${esc(r.draft.description||r.draft.purpose||r.error)}</div>${r.possible_duplicate_ids?.length?'<span class="badge warning">重複の可能性</span>':''}</td><td><span class="account-chip">${esc(r.draft.account||'未確定')}</span></td><td class="num">${yen(r.draft.total)}</td><td class="num">${r.draft.business_percent==null?'未確認':r.draft.business_percent+'%'}</td><td class="num table-main">${r.draft.business_percent==null||r.draft.total==null?'—':yen(r.business_amount)}</td><td><button class="text-button" data-open-receipt="${esc(r.id)}">詳細を修正</button></td></tr>`).join('')||'<tr><td colspan="9" class="empty-table">'+(all.length?'条件に合う明細がありません。検索や期間を変えてください。':'領収書を取り込むと、未確認の明細がここに表示されます。')+'</td></tr>';}
function ledgerCountLabel(rows){return `${state.month||'全期間'} / ${rows.length}件${rows.length>100?' / 100件ずつ表示':''}`;}
function ledgerPaging(pages){return pages>1?`<div class="pagination"><button class="button small" data-action="ledger-prev" ${state.ledgerPage?'':'disabled'}>前の100件</button><span tabindex="-1" aria-live="polite">${state.ledgerPage+1} / ${pages}ページ</span><button class="button small" data-action="ledger-next" ${state.ledgerPage<pages-1?'':'disabled'}>次の100件</button></div>`:'';}

function refreshMonth(){
  if(state.view!=='ledger'){render();return;}
  const input=$('#month-filter');if(input&&input.value!==state.month)input.value=state.month;
  $('[data-action="all-months"]')?.setAttribute('aria-pressed',String(!state.month));
  const context=$('#main-content .ledger-context');if(context)context.outerHTML=ledgerContext();
  updateLedgerTable(true);updateExportPreview();
}
function focusPager(selector,action){
  const pager=$(selector);if(!pager)return;
  const button=$(`[data-action="${action}"]`,pager),target=button&&!button.disabled?button:$('span',pager);
  target?.focus({preventScroll:true});
}
function ledgerContext(){
  const rows=ledgerRows(state.month),pending=state.records.filter(r=>isPending(r)&&(!state.month||!r.draft.date||r.draft.date.startsWith(state.month))).length;
  return `<div class="ledger-context"><span>帳簿の明細 <strong>${rows.length}件</strong></span><span>未確認 <strong>${pending}件</strong></span><span>確認済みの経費 <strong>${yen(summaryFor(state.month).expenses)}</strong><small>事業分・税込、資産候補を除く</small></span></div>`;
}
function refreshLedgerDisplay(){
  if(state.view!=='ledger')return;
  const context=$('#main-content .ledger-context');if(context)context.outerHTML=ledgerContext();
  if(!state.searchComposing&&!state.selected.size){updateLedgerTable();state.ledgerNeedsRefresh=false;}else state.ledgerNeedsRefresh=true;
}

function updateLedgerTable(resetScroll=false){
  if(state.view!=='ledger'||!$('#ledger-rows'))return;
  const {all,filtered,pages,rows,selectable}=ledgerSelection(),table=$('#ledger-table'),top=table.scrollTop,left=table.scrollLeft;
  $('#ledger-rows').innerHTML=ledgerBody(rows,all);
  $('#ledger-count').textContent=ledgerCountLabel(filtered);
  $('#ledger-page-controls').innerHTML=ledgerPaging(pages);
  $('#select-ledger-all').disabled=!selectable.length;
  $('#select-ledger-all').checked=!!selectable.length&&selectable.every(r=>state.selected.has(r.id));
  table.scrollTop=resetScroll?0:top;table.scrollLeft=left;
}

function renderLedger(content) {
  if(!state.records.length){content.innerHTML='<section class="panel results-empty"><h2>CSVに書き出す明細はまだありません</h2><p>領収書を取り込み、原本と候補を照合すると、ここで形式と期間を選べます。</p><a class="button primary" href="#home" data-view="home">領収書を追加する</a></section>';return;}
  const {all,filtered,pages,rows,selectable}=ledgerSelection();
  content.innerHTML=ledgerContext()+`<section class="panel"><div class="table-toolbar"><div class="toolbar-group"><input id="ledger-search" class="filter-input" type="search" value="${esc(state.ledgerSearch)}" placeholder="支払先・科目・ファイル名で検索" aria-label="帳簿を検索"></div><div class="toolbar-group"><select class="filter-input" id="ledger-filter" aria-label="確認状況で絞り込む"><option value="all">未確認を含むすべて</option><option value="review">未確認</option><option value="confirmed">確認済み</option><option value="error">読み取りに失敗</option><option value="excluded">対象外</option></select><span class="table-sub" id="ledger-count">${esc(ledgerCountLabel(filtered))}</span></div></div><div class="table-scroll" id="ledger-table"><table><thead><tr><th><input id="select-ledger-all" type="checkbox" aria-label="表示中の未確認明細をすべて選択" ${selectable.length?'':'disabled'} ${selectable.length&&selectable.every(r=>state.selected.has(r.id))?'checked':''}></th><th>確認状況</th><th>取引日</th><th>支払先 / 購入内容</th><th>勘定科目</th><th class="num">領収書合計</th><th class="num">事業割合</th><th class="num">事業分金額</th><th></th></tr></thead><tbody id="ledger-rows">${ledgerBody(rows,all)}</tbody></table></div><div id="ledger-page-controls">${ledgerPaging(pages)}</div></section><p class="ledger-note">金額と科目は確認が済むまで候補です。原本と照らしてから確認済みにしてください。取引日が未確定の明細は、月を選んだ一覧にも残します。</p><details class="panel settings-card" id="batch-review-details" ${state.selected.size||state.batchReport?'open':''}><summary id="batch-selection">選択した明細をまとめて確認（${state.selected.size}件）</summary><p>選択した明細のうち空欄になっている項目へだけ、共通の用途・事業割合・支払元・税区分を設定します。すでに入力された値は上書きしません。</p><div class="form-row"><label class="field"><span class="field-label">共通の用途（空欄のみ）</span><input id="batch-purpose" maxlength="1000" placeholder="例：制作の業務で使用"></label><label class="field"><span class="field-label">事業割合（空欄のみ・%）</span><input id="batch-percent" type="number" min="1" max="100" placeholder="例：仕事専用なら100"><span class="field-hint">確認済みにする割合は1〜100%。全額私用の明細は「選択分を対象外にする」を使います。</span></label></div><label class="field"><span class="field-label">支払元（空欄のみ）</span><select id="batch-payment"><option value="">各明細の入力を使う</option>${Object.entries(state.config.payments).map(([k,v])=>`<option value="${k}">${esc(v.label)}</option>`).join('')}</select></label><label class="field"><span class="field-label">会計ソフト出力用の税区分（空欄のみ・任意）</span><select id="batch-tax">${Object.entries(state.config.export_tax_choices).map(([k,v])=>`<option value="${esc(k)}">${esc(k?v:'各明細の入力を使う')}</option>`).join('')}</select><span class="field-hint">税率の混在は個別に確認します。税区分は自動では決めません。</span></label><div class="import-actions"><button class="button" data-action="review-ledger">選択内容を確認する</button><button class="button" data-action="exclude-selected">選択分を対象外にする</button><button class="text-button" data-action="clear-selection">選択をすべて解除</button><button class="text-button" data-action="restore-selected">対象外から戻す</button></div><div id="batch-result" aria-live="polite"></div></details>${exportPanel()}`;
  $('#ledger-filter').value=state.ledgerFilter;
  renderBatchReport();
  if(state.batchOnlyIssues)$('#ledger-count').insertAdjacentHTML('afterend','<button class="text-button" data-action="clear-issue-filter">残った明細の絞り込みを解除</button>');
  content.prepend($('#export-panel'));
  for(const key of ['purpose','percent','payment','tax'])if(state.batch[key]!=null)$('#batch-'+key).value=state.batch[key];
  updateExportPreview();
}

function batchMissing(row){
  const draft=row.draft,percent=draft.business_percent??state.batch.percent;
  return [
    [!draft.date,'取引日'],[!draft.account,'勘定科目'],
    [draft.total==null||draft.total<=0,'金額'],
    [!draft.purpose?.trim()&&!state.batch.purpose?.trim(),'業務での用途'],
    [percent==null||percent===''||!Number.isInteger(Number(percent))||Number(percent)<=0||Number(percent)>100,'事業割合（1〜100%）'],
    [!draft.payment&&!state.batch.payment,'支払元'],
  ].filter(([missing])=>missing).map(([,label])=>label);
}

function openBatchReview(){
  if(!state.selected.size){toast('帳簿の左端で、確認する明細を選択してください。',true);return;}
  rememberLedgerInputs();
  const rows=[...state.selected.keys()].map(id=>state.records.find(r=>r.id===id)).filter(Boolean),dialog=$('#review-dialog');
  const missingCount=rows.filter(r=>batchMissing(r).length).length;
  dialog.innerHTML=`<form method="dialog" class="dialog-heading"><h2>${rows.length}件を原本と照合</h2><button class="button small">戻る</button></form><p>各明細の日付・金額・科目と、用途・事業割合・支払元を確認してください。読み取りとAIの結果は候補なので、そのまま税務上の結論に使わないでください。</p>${missingCount?`<div class="warning-list" id="batch-missing-warning" role="alert">${missingCount}件に入力の不足があります。必須項目をそろえた明細だけを確認済みにし、残りは未確認のまま保持します。「戻る」で共通項目を補うか、各明細の「詳細を修正」で入力してください。</div>`:''}<div class="review-check-list">${rows.map(r=>`<article>${r.preview_count?`<img loading="lazy" data-private-src="/api/receipts/${r.id}/preview/0" alt="${esc(r.original_name)}の原本">`:''}<div><strong>${esc(r.draft.merchant||r.original_name)} · ${yen(r.draft.total)}</strong><p>${esc(r.draft.date||'日付未確定')} / ${esc(r.draft.account||'科目未確定')}</p><p>用途：${esc(r.draft.purpose||state.batch.purpose||'未入力')} / 事業割合：${esc(r.draft.business_percent??state.batch.percent??'未入力')}% / 支払元：${esc(state.config.payments[r.draft.payment||state.batch.payment]?.label||'未入力')}</p>${batchMissing(r).length?`<p class="review-warning">不足：${batchMissing(r).map(esc).join('・')}</p>`:''}${r.suggestion?.warnings?.length?`<p class="review-warning">${r.suggestion.warnings.map(esc).join(' / ')}</p>`:''}${r.possible_duplicate_ids?.length?'<p class="review-warning">日付・支払先・金額が一致する明細があります。別の支出か、二重取り込みかを原本で確認してください。自動では削除しません。</p>':''}<button class="text-button" data-review-open="${r.id}">詳細を修正</button></div></article>`).join('')}</div><label class="checkbox-line"><input id="review-ack" type="checkbox">上記の原本と照合し、金額・勘定科目・用途・事業割合・支払元を自分で確認しました（チェックすると下のボタンを押せます）</label><button class="button primary" id="confirm-batch" data-action="confirm-batch" disabled>確認して保存（最大${rows.length}件）</button><p class="field-hint">必須入力の不足や他の操作との競合があった明細は、理由を示して未確認のまま残します。申告・税務処理の最終判断は利用者と税理士が行ってください。</p>`;
  dialog.showModal();
}

async function reviewLedger(){
  const rows=[...state.selected].map(([id,version])=>({id,version})),reviewed=[],issues=[];
  let interrupted=false,refreshFailed=false;
  for(let offset=0;offset<rows.length;offset+=100){
    try{
      const result=await api('/ledger/review',{method:'POST',body:JSON.stringify({rows:rows.slice(offset,offset+100),purpose:state.batch.purpose?.trim()||'',payment:state.batch.payment||'',business_percent:state.batch.percent?Number(state.batch.percent):null,export_tax:state.batch.tax||''})});
      reviewed.push(...result.reviewed);issues.push(...result.issues);
      for(const id of result.reviewed)state.selected.delete(id);
    }catch(error){
      interrupted=true;
      issues.push(...rows.slice(offset).map(row=>({id:row.id,message:'通信が中断しました。現在の保存状態を確認し、未確認の明細を改めて確認してください。'})));
      break;
    }
  }
  try{
    await loadReceipts();
    for(const row of rows)if(state.records.find(record=>record.id===row.id)?.status==='confirmed')state.selected.delete(row.id);
  }catch(error){refreshFailed=true;}
  state.batchReport={action:'review',count:reviewed.length,issues,interrupted,refreshFailed};state.batchOnlyIssues=false;
  $('#review-dialog').close();render();
}

function batchIssues(){
  const target={review:'confirmed',exclude:'excluded',restore:'review'}[state.batchReport?.action];
  return (state.batchReport?.issues||[]).filter(issue=>state.records.find(row=>row.id===issue.id)?.status!==target);
}
function renderBatchReport(){
  const el=$('#batch-result'),report=state.batchReport;if(!el||!report)return;
  const issues=batchIssues(),verb={review:'確認済みにしました',exclude:'対象外にしました',restore:'未確認に戻しました'}[report.action];
  el.innerHTML=`<p class="result-status">${report.interrupted?`保存完了の応答を確認できた明細：${report.count}件。`:`${report.count}件を${verb}。`}</p>${report.interrupted?'<p class="warning-list" role="alert">通信が中断したため、後続の一括確認を行わずに停止しました。応答が返った分の明細は保存済みです。最新の状態を取得し、残りをもう一度確認してください。</p>':''}${report.refreshFailed?'<p class="warning-list" role="alert">最新の保存状態を取得できませんでした。中断の直前に保存が完了した明細がある可能性があります。ページを再読み込みして、残りの明細を確認してください。</p>':''}${issues.length?`<p class="ledger-note">前回の一括処理で未完了の明細：${issues.length}件。このタブを閉じるまでは結果を保持します。内容を直したら、もう一度確認操作を行ってください。</p><button class="button small" data-action="filter-batch-issues">残った明細だけ表示（${issues.length}件）</button>${issues.slice(0,10).map(issue=>{const row=state.records.find(record=>record.id===issue.id);return `<p class="ledger-note batch-issue"><button class="text-button" data-open-receipt="${esc(issue.id)}">${esc(row?.original_name||'明細を開く')} ↗</button><br>${esc(row?.draft.merchant||'支払先未確定')} / ${esc(row?.draft.date||'日付未確定')} / ${yen(row?.draft.total)}<br>${esc(issue.message)}</p>`;}).join('')}${issues.length>10?'<p class="field-hint">最初の10件だけをここに表示しています。ほかの明細は「残った明細だけ表示」で開いてください。</p>':''}`:''}<button class="text-button" data-action="dismiss-batch-result">この結果を閉じる</button>`;
}

function exportPanel(){
  const profiles=state.config.export_profiles||{generic:{label:'汎用CSV'}};
  const native=state.exportProfile!=='generic';
  return `<section class="panel export-panel" id="export-panel"><div class="export-heading"><div><h2>CSVの形式と範囲</h2></div></div><div class="export-controls"><label class="field"><span class="field-label">取り込み先</span><select id="export-profile">${Object.entries(profiles).map(([k,v])=>`<option value="${esc(k)}" ${k===state.exportProfile?'selected':''}>${esc(v.label)}</option>`).join('')}</select></label>${native?`<label class="field"><span class="field-label">消費税の扱い</span><select id="export-tax-mode"><option value="reviewed" ${state.exportTaxMode==='reviewed'?'selected':''}>各領収書で確認した区分を使う</option><option value="exempt" ${state.exportTaxMode==='exempt'?'selected':''}>免税事業者として全行を対象外にする</option></select><span class="field-hint">免税事業者の税込記帳では、上の「全行を対象外にする」を選ぶと、個別の税区分入力を省略できます。</span></label>`:`<label class="field"><span class="field-label">出力する内容</span><select id="export-kind"><option value="expenses" ${state.exportKind==='expenses'?'selected':''}>確認済みの経費帳</option><option value="journal" ${state.exportKind==='journal'?'selected':''}>確認済みの仕訳</option><option value="review" ${state.exportKind==='review'?'selected':''}>明細一覧（未確認を含む）</option></select></label>`}<div class="field export-period"><span class="field-label">対象の期間</span>${monthControl()}</div></div><p class="field-hint">${esc(state.exportProfile==='generic'?'経費帳・仕訳・明細一覧':profiles[state.exportProfile]?.route||'')} · ${esc(profiles[state.exportProfile]?.encoding||'UTF-8 BOM')}</p><label class="checkbox-line"><input id="export-new-only" type="checkbox" ${state.exportNewOnly?'checked':''}>この形式でCSVを作成した明細を除く（修正後の明細は含む）</label><p class="field-hint">この履歴はアプリがCSVを生成した記録で、ブラウザーへの保存や会計ソフトへの取り込みを確認したものではありません。検索条件と明細のチェックは出力対象に影響しません。</p><p class="field-hint export-tax-note">CSV作成は税理士の承認を意味しません。書き出したCSVは暗号化されない通常のファイルなので、保存先と共有先を管理してください。</p>${exportMapPanel()}<div id="export-result" aria-live="polite"><p class="ledger-note">出力内容を確認しています…</p></div><div class="export-save"><button class="button primary" data-action="download-csv" id="download-csv" disabled>CSVを保存</button></div></section>`;
}

function exportMapPanel(){
  if(state.exportProfile==='generic')return '';
  const mapping=state.config.export_account_maps?.[state.exportProfile]||{};
  return `<details class="export-sample"><summary>科目の出力名を合わせる（任意）</summary><p class="ledger-note">取り込み先の名称へ対応付けます。1行ごとに「元の科目=出力先の科目」と入力してください。対応は形式ごとに帳簿の暗号化DBへ保存し、科目マスターは変更しません。</p><label class="field"><span class="field-label">科目の対応</span><textarea id="export-account-map" placeholder="消耗品費=事務用品費">${esc(state.exportMapDrafts.get(state.exportProfile)??Object.entries(mapping).map(([k,v])=>k+'='+v).join('\n'))}</textarea></label><button class="button small" data-action="save-export-map">出力名を保存</button></details>`;
}

function exportQuery(){
  return new URLSearchParams({new_only:String(state.exportNewOnly),profile:state.exportProfile,kind:state.exportProfile==='generic'?state.exportKind:'journal',month:state.month,tax_mode:state.exportTaxMode});
}

async function updateExportPreview(){
  const revision=++state.exportRevision;
  if($('#download-csv'))$('#download-csv').disabled=true;
  try{
    const expanded=$('#export-result .export-sample')?.open;
    const result=await api('/export-preview?'+exportQuery());
    if(revision!==state.exportRevision||!$('#export-result'))return;
    $('#download-csv').disabled=!result.ready||!result.receipt_count||!!state.csvSaving;
    $('#export-result').innerHTML=`${result.csv_history?.last_created_at?`<p class="field-hint">前回CSV作成の日時（同じ形式）：${esc(new Date(result.csv_history.last_created_at).toLocaleString('ja-JP'))}${state.exportNewOnly?' / 作成済みの版を除外しています':''}</p>`:''}<div class="export-totals"><strong>${result.receipt_count}件 / ${result.row_count}行</strong>${state.exportProfile!=='generic'?`<span>借方 ${yen(result.debit_total)} / 貸方 ${yen(result.credit_total)}</span>`:''}<span>${state.month?esc(state.month):'全期間'}</span></div>${result.issues.length?`<div class="error-box"><strong>出力前に${result.issues.length}点の確認が必要です</strong>${result.issues.map(v=>`<p><button class="text-button" data-fix-export="${esc(v.id)}">${esc(v.file)}</button> ${esc(v.message)}</p>`).join('')}</div>`:''}${state.exportProfile!=='generic'&&state.records.some(r=>isPending(r))?'<p class="warning-list">会計ソフト向けの仕訳CSVは確認済みの明細だけを出力します。未確認の下書きも必要なら「汎用CSV」→「明細一覧（未確認を含む）」を選んでください。</p>':''}${result.warnings.map(v=>`<p class="ledger-note">${esc(v.replace('確認待ち・対象外を含む候補の一覧です。','未確認・対象外を含む候補の一覧です。'))}</p>`).join('')}${result.rows.length?`<details class="export-sample"><summary>CSVの先頭${result.rows.length}行を確認する</summary><div class="table-scroll"><table><thead><tr>${result.columns.map(v=>`<th>${esc(v)}</th>`).join('')}</tr></thead><tbody>${result.rows.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`:`<p class="ledger-note">${state.exportProfile==='generic'&&state.exportKind==='review'?'選択した期間と除外設定では、書き出せる明細がありません。期間または「この形式でCSVを作成した明細を除く」の設定を変えてください。':'この条件には出力可能な確認済み明細がありません。明細を原本と照合して確認済みにするか、期間・作成済みの除外設定を見直してください。'}</p>`}`;
    if(expanded&&$('#export-result .export-sample'))$('#export-result .export-sample').open=true;
  }catch(error){if(revision===state.exportRevision&&$('#export-result')){if($('#download-csv'))$('#download-csv').disabled=true;$('#export-result').innerHTML=`<div class="error-box">${esc(error.message)}</div>`;}}
}

async function downloadCsv(){
  if(state.csvSaving)return;
  state.csvSaving=true;
  const button=$('#download-csv'),profile=state.exportProfile,month=state.month;
  if(button)button.disabled=true;
  try{
  const query=exportQuery(), kind=query.get('kind');
  const response=await RyoshuSecurity.fetch(`/api/export/${kind}?${query}`,{method:"POST",headers:{"X-Ledger-Request":"1"}});
  if(!response.ok){const result=await response.json();throw new Error(result.detail||'CSVを作成できませんでした');}
  const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
  link.href=url;link.download=`${profile}-${kind}-${month||'all'}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  toast('CSVを作成しました。保存場所を確認し、会計ソフトへ取り込む前後で件数と金額を照合してください。');
  }finally{state.csvSaving=false;if($('#download-csv'))updateExportPreview();}
}

function renderAccounts(content) {
  const totals=new Map();
  state.records.filter(r=>r.status==='confirmed'&&(r.draft.date||'').startsWith(state.month)).forEach(r=>{const v=totals.get(r.draft.account)||{amount:0,count:0};v.amount+=r.business_amount;v.count++;totals.set(r.draft.account,v);});
  const rows=[...totals].sort((a,b)=>b[1].amount-a[1].amount),max=rows[0]?.[1].amount||1;
  content.innerHTML=stats(state.month)+`<div class="account-grid"><section class="panel"><div class="table-toolbar">${monthControl()}<span class="table-sub">事業分・税込</span></div><div class="category-chart">${rows.map(([name,v])=>`<div class="category-row"><div class="category-top"><span>${esc(name)}${state.config.accounts.some(a=>a.name===name&&a.kind==='asset')?'（資産）':''}</span><strong>${yen(v.amount)}</strong></div><div class="category-bar"><span style="width:${v.amount/max*100}%"></span></div><div class="category-count">${v.count}件</div></div>`).join('')||'<div class="empty-table">対象期間に確認済みの明細があると、科目ごとの集計がここに表示されます。</div>'}</div></section><section class="panel notes-panel"><div class="section-kicker">集計のポイント</div><h3>事業で使った分を、科目ごとに。</h3><p>集計には確認済みの明細と入力された事業割合を使い、事業分の1円未満を四捨五入します。</p><p>資産に分類した科目は経費の合計に含めず別に扱います。減価償却の計算は行いません。</p><p>科目はAIの候補です。同じ店の領収書でも購入内容が違えば科目が変わるため、1件ずつ原本と照合してください。</p><a class="text-button" href="https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/2210.htm" target="_blank" rel="noreferrer">必要経費について（国税庁） ↗</a></section></div>`;
}

function renderSettings(content) {
  const h=state.health,c=state.config;
  content.innerHTML=`<div class="settings-layout"><div><section class="panel settings-card"><h2>事業の概要</h2><p>仕事の内容を短く入力すると、次回からAIが購入内容との関係を考えて科目候補を作ります。取引先の秘密や個人情報は書かないでください。</p><label><span class="field-label">事業の概要（任意）</span><textarea class="settings-textarea" id="business-context" maxlength="1500" placeholder="例：ゲーム制作・イラスト制作。資料書籍や制作ソフトを使用。">${esc(c.business_context)}</textarea></label><button class="button primary" data-action="save-settings">保存する</button><div class="field-hint">次回の読み取りから使います。既存の候補を更新するには再読み取りしてください。</div></section><details class="panel settings-card"><summary>支払先ごとの科目設定</summary><p>「この支払先の科目を次回の候補にする」を選んだ支払先だけを表示しています。今後の読み取りでは、店舗名と購入内容を照合して科目候補に使います。</p>${c.rules.map(r=>`<div class="rule-row"><span>${esc(r.merchant)}<small>${esc(r.account)}</small></span><button class="text-button" data-remove-rule="${esc(r.merchant)}">解除</button></div>`).join('')||'<p>まだルールはありません。</p>'}</details><section class="panel settings-card"><h2>暗号化バックアップ</h2><p>帳簿・原本・読み取り結果・修正履歴を1つのファイルにまとめ、復元用パスワードで保護します。ファイルとパスワードは別々の安全な場所へ保管してください。</p><details><summary>帳簿の保存先を見る</summary><div class="storage-path">${esc(h?.data_dir||'')}</div></details><button class="button" data-action="backup">暗号化バックアップを保存 ↓</button><p><button class="text-button" data-action="change-password">復元用パスワードを忘れた・変更したい</button></p><p class="field-hint">戻すときは「バックアップから復元.cmd」で .ryoshubak を選び、作成時の復元用パスワードを入力します。現在の帳簿を上書きせず、別の保存先へ復元します。書き出したCSVと原本は暗号化されない通常のファイルなので、保存先と共有先を管理してください。</p></section></div><div><section class="panel settings-card"><h2>領収書はローカルPC上で処理</h2><p>Indra OCRで文字を読み、Ollama上のQwen3 4Bが科目候補を作ります。領収書の画像と内容を外部AIへ送らず、クラウドへの自動切替やアクセス解析もありません。外部AIのアカウントとAPIキーは不要です。</p><p>初回準備では実行環境・依存・OCRモデル・約2.5GBのQwenモデルを取得するためネット接続が必要です。アプリは127.0.0.1:8766、Ollamaは127.0.0.1:11434を使います。</p><p>帳簿DB・保存済みの原本・プレビュー・暗号化バックアップは暗号化します。書き出したCSVと原本は暗号化されない通常のファイルなので、保存先と共有先を管理してください。</p></section><details class="panel settings-card"><summary>ローカルの実行環境</summary><div class="system-row"><span>文字の読み取り</span><strong>Indra OCR ${esc(h?.ocr_version||'—')}</strong></div><div class="system-row"><span>勘定科目の候補作成</span><strong>Qwen3 4B</strong></div><div class="system-row"><span>モデルの状態</span><strong>${h?.model_ready?'準備できています':'Ollama・モデルを確認してください'}</strong></div><div class="system-row"><span>実行デバイス</span><strong>CPU</strong></div><div class="system-row"><span>帳簿の保存</span><strong>SQLCipher / 暗号化</strong></div><div class="system-row"><span>アプリ</span><strong>${esc(h?.app_version||'確認できません')}</strong></div><p class="ledger-note">領収書の画像と本文は外部AIへ送信しません。初回準備ではOCRとQwenのモデルをダウンロードします。</p></details><details class="panel settings-card"><summary>帳簿を作る順序</summary><p>標準の流れでは、取り込んだ明細を未確認の下書きとして帳簿へ保存し、あとから一覧で照合します。</p><label class="checkbox-line"><input id="review-before-ledger" type="checkbox" ${state.config.review_before_ledger?'checked':''}>1件ずつ確認してから帳簿へ並べる</label><p class="field-hint">チェックの変更は自動で保存します。</p></details><details class="panel settings-card"><summary>対応している作業</summary><p>個人事業主の支出を対象に、領収書の読み取り、科目候補の確認、税込の経費帳と経費仕訳CSVを作成します。</p><p>売上・口座照合、減価償却、決算・申告書、返金・外貨の仕訳は対象外です。電子帳簿保存法への適合を認証・保証する製品ではありません。</p><p>科目と必要経費への算入可否は、実際の業務内容に照らして判断し、利用者が登録します。</p></details></div></div>`;
  const samples=state.config.samples||{count:100,description:'架空の領収書で取込を試せます。'};
  $('.settings-layout > div',content)?.insertAdjacentHTML('beforeend',`<details class="panel settings-card"><summary>${samples.count}種類の架空領収書</summary><p><a href="/evidence/security/">テスト画像・期待値・100件の結果を見る ↗</a></p><p>${esc(samples.description)} 実際の取引ではありません。</p><div class="sample-actions">${samples.count===100?'<a class="button" href="/samples/receipts-100-v2/gallery.html">100パターンを一覧で見る ↗</a>':''}<a class="button" href="/api/samples/zip">個別PDF ${samples.count}件を保存 ↓</a><a class="button" href="/api/samples/review">${samples.count}ページの合本を見る ↗</a></div><p class="field-hint">ZIPを展開すると、個別PDFをフォルダーごと選択できます。合本は「1ページずつ、別の領収書にする」を選んで取り込んでください。期待値・業務背景・作成モデルの記録も同梱しています。</p></details>`);
}

const kindLabels={expense:'経費',asset:'資産',cash:'支払元',bank:'支払元',liability:'負債',equity:'事業主勘定'};
function renderCatalog(content){
  const catalog=state.catalog;
  if(!catalog){content.innerHTML='<section class="panel settings-card">マスターを読み込んでいます…</section>';return;}
  const accounts=catalog.accounts;
  content.innerHTML=`<div class="summary-grid"><div class="stat-card"><div class="stat-label">登録されている科目</div><strong>${accounts.length}<small>科目</small></strong><div class="stat-caption">経費・資産・支払元・事業主勘定</div></div><div class="stat-card featured"><div class="stat-label">確認条件のデータ</div><strong>${catalog.policies.length}<small>種類</small></strong><div class="stat-caption">飲食・返金・家事按分・資産など</div></div><div class="stat-card"><div class="stat-label">保存と参照</div><strong class="catalog-db-label">SQLite</strong><div class="stat-caption">画面・AI候補・仕訳が同じマスターを参照</div></div></div><section class="panel"><div class="table-toolbar"><div class="toolbar-group"><select class="filter-input" id="catalog-category" aria-label="科目のカテゴリー">${accountCategoryOptions(accounts,state.catalogCategory,'すべてのカテゴリー')}</select><input class="filter-input" id="catalog-search" type="search" value="${esc(state.catalogSearch)}" placeholder="科目名・別名・用途で検索" aria-label="科目を検索"><span class="table-sub" id="catalog-count"></span></div><span class="table-sub">DBスキーマ ${catalog.schema_version} / 初期データ ${esc(catalog.seed_version)}</span></div><div class="table-scroll catalog-scroll"><table class="catalog-table"><thead><tr><th>コード / 科目</th><th>区分</th><th>主な用途</th><th>確認すること・別名</th></tr></thead><tbody id="catalog-rows"></tbody></table></div></section><details class="panel settings-card catalog-add"><summary>自分用の勘定科目を追加する</summary><p class="field-hint">初期科目と既存の編集内容を変えずに、独自の経費・資産科目を追加できます。追加した科目はAIの候補と明細の選択肢に使えます。</p><form id="account-form"><div class="form-row"><label class="field"><span class="field-label">科目名</span><input name="name" maxlength="100" required placeholder="例：制作サービス利用料"></label><label class="field"><span class="field-label">区分</span><select name="kind" id="account-add-kind"><option value="expense">経費</option><option value="asset">資産</option></select></label></div><label class="field"><span class="field-label">カテゴリー</span><select name="category_id" id="account-add-category">${addAccountCategoryOptions('expense')}</select></label><label class="field"><span class="field-label">用途・例</span><textarea name="description" maxlength="1000" placeholder="この科目で記録する支出の内容"></textarea></label><label class="field"><span class="field-label">別名（任意・カンマ区切り）</span><input name="aliases" placeholder="サービス利用料, ツール利用料"></label><label class="field"><span class="field-label">品目の手がかり（任意・カンマ区切り）</span><input name="keywords" placeholder="例：制作素材ライセンス, テクスチャ素材"></label><label class="field"><span class="field-label">確認ポイント（任意・1行に1項目）</span><textarea name="review_questions" placeholder="事業で使うサービスか\n当月分の支払か"></textarea></label><button class="button primary" type="submit">科目を追加</button></form></details><section class="panel settings-card"><h2>候補を確かめるための条件</h2><p>科目候補には確認条件も表示します。業務用途や必要経費への算入可否を、領収書とAIだけで確定することはありません。</p><div class="policy-grid">${catalog.policies.map(p=>`<article class="policy-card"><div class="suggestion-label"><span>${esc(p.code)}</span><span>${p.action==='block'?'通常の経費登録を停止':p.action==='hold'?'科目を保留':'確認事項を表示'}</span></div><h3>${esc(p.title)}</h3><p>${esc(p.message)}</p></article>`).join('')}</div><div class="catalog-sources"><span class="field-label">考え方の参照資料</span>${catalog.sources.map(s=>`<p><a href="${esc(s.url)}" target="_blank" rel="noreferrer">${esc(s.title)} ↗</a><span class="table-sub"> · 確認 ${esc(s.checked_at)}</span></p>`).join('')}<p class="field-hint">科目名・用途の例・確認事項はRyoshuの初期データです。実際の税務上の取扱いを一律に決める一覧ではありません。</p></div></section>`;
  renderCatalogRows(state.catalogSearch);
}

function renderCatalogRows(query){
  const el=$('#catalog-rows');if(!el)return;
  state.catalogSearch=String(query);
  const normalized=state.catalogSearch.normalize('NFKC').toLocaleLowerCase();
  const rows=state.catalog.accounts.filter(a=>(!state.catalogCategory||accountCategoryId(a)===state.catalogCategory)&&[a.name,a.code,a.description,...a.aliases,...(a.hints?.any||[])].join(' ').normalize('NFKC').toLocaleLowerCase().includes(normalized));
  $('#catalog-count').textContent=`${rows.length}科目`;
  el.innerHTML=rows.map(a=>`<tr><td><div class="table-sub">${esc(a.code)}${a.seeded?'':' / 自分の科目'}</div><div class="catalog-account-name">${catalogFavoriteButton(a)}<strong>${esc(a.name)}</strong></div></td><td><span class="account-chip">${esc(accountDefinitions().find(c=>c.id===accountCategoryId(a))?.label||'その他')}</span><div class="table-sub">${esc(kindLabels[a.kind]||a.kind)}</div></td><td class="catalog-description">${esc(a.description)}</td><td class="catalog-description">${a.review_questions.map(q=>`<div>· ${esc(q)}</div>`).join('')||'<span class="table-sub">支払元・仕訳の相手科目として使用</span>'}${a.hints?.any?.length?`<div class="field-hint">品目の手がかり：${esc(a.hints.any.join(' / '))}</div>`:''}${a.aliases.length?`<div class="table-sub">別名：${esc(a.aliases.join(' / '))}</div>`:''}</td></tr>`).join('')||'<tr><td colspan="4" class="empty-table">一致する科目はありません</td></tr>';
}

async function addAccount(form){
  const data=Object.fromEntries(new FormData(form));
  const button=$('button[type="submit"]',form);button.disabled=true;
  try{
    await api('/accounts',{method:'POST',body:JSON.stringify({name:data.name.trim(),kind:data.kind,category_id:data.category_id||'',description:data.description.trim(),aliases:data.aliases.split(/[,、\n]/).map(v=>v.trim()).filter(Boolean),keywords:(data.keywords||'').split(/[,、\n]/).map(v=>v.trim()).filter(Boolean),review_questions:data.review_questions.split('\n').map(v=>v.trim()).filter(Boolean)})});
    [state.catalog,state.config]=await Promise.all([api('/catalog'),api('/config')]);render();toast('勘定科目をデータベースに追加しました。');
  }catch(error){toast(error.message,true);}finally{button.disabled=false;}
}

function captureForm() {
  const form=$('#review-form'); if(!form||!state.detail)return null;
  const values=Object.fromEntries(new FormData(form));
  const draft={...currentDraft()};
  for(const key of new Set([...Object.keys(state.detail.draft),'export_tax'])) if(key in values) draft[key]=['total','tax_amount','business_percent'].includes(key)?(values[key]===''?null:Number(values[key])):values[key].trim();
  const accountChoice=$('[data-account-choice]',form);if(accountChoice)draft.account=accountChoice.value;
  return {version:state.drafts.get(state.active)?.version||state.detail.version,draft,remember_account:values.remember_account==='on'};
}
function trackEdit() {
  const body=captureForm();if(!body)return;
  state.drafts.set(state.active,body);$('#save-status').textContent='変更はまだ保存していません。';$('#save-status').className='unsaved';
  const d=body.draft;$('#business-amount').textContent=d.total!=null&&d.business_percent!=null?yen(Math.round(d.total*d.business_percent/100)):'—';
  renderJournal(d);renderQueue();
}

async function loadReceipts() {
  const revision=++receiptRevision;
  try{const result=await api('/receipts');if(revision===receiptRevision){state.records=result.receipts;state.processingFetchedAt=Date.now();}}
  catch(error){if(revision===receiptRevision)processingUI.connectionFailed();throw error;}
}
async function selectReceipt(id) {
  const origin=state.view!=='inbox'?{view:state.view,scroll:window.scrollY}:null;
  const revision=++detailRevision,detail=await api('/receipts/'+id);if(revision!==detailRevision)return;
  state.active=id;state.detail=detail;state.sourceTab='image';state.page=0;state.history=null;
  if(origin)state.detailOrigin=origin;
  if(state.view!=='inbox')state.view='inbox';render();if(origin)focusPage();return true;
}
async function save(confirm,next=false) {
  if(state.busy){toast('保存中です。完了してからもう一度操作してください。');return;}
  const body=captureForm();if(!body)return;
  const id=state.active,revision=detailRevision,submittedDraft=JSON.stringify(body.draft);
  let saved=false;
  state.busy=true;const buttons=$$('#review-form button');buttons.forEach(b=>b.disabled=true);
  try {
    const result=await api(`/receipts/${id}/save`,{method:'POST',body:JSON.stringify({...body,confirm})});
    saved=true;if(state.detail?.id===id)state.detail=result;
    state.selected.delete(id);
    const pending=state.drafts.get(id);
    if(!pending||JSON.stringify(pending.draft)===submittedDraft)state.drafts.delete(id);
    else state.drafts.set(id,{...pending,version:result.version});
    await loadReceipts();
    if(body.remember_account&&confirm)state.config=await api('/config');
    if(revision!==detailRevision||state.active!==id||state.view!=='inbox'){render();toast('明細を保存しました。現在の画面と未保存の入力は保持しています。');return;}
    if(confirm&&next){const row=nextReviewReceipt(state.active);if(row)await selectReceipt(row.id);else{state.view='ledger';render();}}else render();toast(confirm?'明細を確認済みにしました。':'変更を保存しました。未確認のまま残しています。');
  }catch(error){if(saved){render();toast('明細は保存しましたが、最新情報を取得できませんでした。ページを再読み込みして確認してください。',true);return;}const box=state.active===id?$('#form-errors'):null;if(box){box.textContent=error.message;box.classList.remove('hidden');box.scrollIntoView({block:'nearest',behavior:'smooth'});}else toast(error.message,true);}
  finally{state.busy=false;buttons.forEach(b=>b.disabled=false);}
}

let importRefreshTimer;
function beginImport(){
  processingUI.begin();
  renderProcessing();
  if(state.view==='inbox'&&state.drafts.size)return;
  if(state.view==='home'){
    renderHome();
    // Starting is an explicit action: show its progress once, never on polls.
    requestAnimationFrame(()=>{if(state.view==='home')focusPage();});
    return;
  }
  state.view=state.config.review_before_ledger?'inbox':'results';render();
}
async function refreshImported(){
  try{
    await loadReceipts();renderProcessing();
    if(state.config.review_before_ledger&&state.view==='inbox'&&!state.active&&state.records.length){await selectReceipt(state.records[0].id);}
    else if(state.view==='ledger'&&!$('#review-dialog').open){if(!$('#ledger-table'))render();else{refreshLedgerDisplay();updateExportPreview();}}
    else{state.ledgerNeedsRefresh=true;if(state.view==='inbox')renderQueue();if(state.view==='home')renderHome();if(state.view==='results')renderResults($('#main-content'),true);}
    schedulePoll();
  }catch{toast('原本は保存しました。一覧を更新できなかったため、接続を確認して再読み込みしてください。',true);}
}
function uploaded(result){
  processingUI.imported(result);
  renderProcessing();
  clearTimeout(importRefreshTimer);importRefreshTimer=setTimeout(refreshImported,300);
  if(!state.records.length)refreshImported();
}

async function setSelectedStatus(action){
  const rows=[...state.selected].map(([id,version])=>({id,version}));
  if(!rows.length){toast('明細の左端のチェックで選択してください。',true);return;}
  if(!confirm(`${rows.length}件を${action==='exclude'?'経費帳の対象外にします。原本は保持し、あとで戻せます。':'未確認へ戻します。'}`))return;
  let count=0;const issues=[];
  for(let offset=0;offset<rows.length;offset+=100){const result=await api('/ledger/status',{method:'POST',body:JSON.stringify({action,rows:rows.slice(offset,offset+100)})});count+=result.changed.length;issues.push(...result.issues);for(const id of result.changed)state.selected.delete(id);}
  state.batchReport={action,count,issues};state.batchOnlyIssues=false;
  await loadReceipts();render();toast(`${count}件を${action==='exclude'?'対象外にしました。':'未確認に戻しました。'}${issues.length?' 処理できなかった明細と理由は、一括処理の結果で確認できます。':''}`,!!issues.length);
}

async function retryErrors(){
  const rows=state.records.filter(r=>r.status==='error'||(r.status==='review'&&r.error)).map(r=>({id:r.id,version:r.version}));
  let count=0;const issues=[];
  for(let offset=0;offset<rows.length;offset+=100){const result=await api('/processing/retry',{method:'POST',body:JSON.stringify({rows:rows.slice(offset,offset+100)})});count+=result.retried.length;issues.push(...result.issues);}
  await loadReceipts();render();schedulePoll();toast(`${count}件の再読み取りを開始しました。${issues.length?' '+issues.map(i=>i.message).join(' / '):''}`,!!issues.length);
}

function schedulePoll(){clearTimeout(pollTimer);if(state.records.some(busyRecord))pollTimer=setTimeout(poll,2200);}
async function poll(){
  try{await loadReceipts();renderProcessing();renderPendingCount();
    const updated=state.records.find(r=>r.id===state.active);
    if(updated&&(busyRecord(state.detail)||updated.version!==state.detail?.version)&&!state.drafts.has(state.active)){
      const id=state.active,revision=detailRevision;
      const detail=await api('/receipts/'+id);
      if(state.active===id&&detailRevision===revision&&!state.drafts.has(id)){state.detail=detail;if(state.view==='inbox')render();}
    }
    else if(state.view==='inbox')renderQueue();
    if(state.view==='ledger'&&!$('#review-dialog').open)refreshLedgerDisplay();
    if(state.view==='home')renderHome();
    if(state.view==='results')renderResults($('#main-content'),true);
  }catch(error){toast('ローカルアプリとの接続を確認してください。',true);}
  schedulePoll();
}

document.addEventListener('click',async event=>{
  const target=event.target.closest('button,a,[data-action],summary[data-ocr-receipt]');if(!target)return;
  try{
    if(target.dataset.view){event.preventDefault();if(!state.uiReady)return;const revision=++detailRevision;if(target.dataset.view==='catalog'){const catalog=await api('/catalog');if(revision!==detailRevision)return;state.catalog=catalog;}state.view=target.dataset.view;history.replaceState(null,'','#'+state.view);$('.more-nav').open=false;render();focusPage();return;}
    if(target.dataset.action==='back-from-detail'){returnFromDetail();return;}
    if(target.dataset.changeAccount){await openAccountChange(target.dataset.changeAccount,target);return;}
    if(target.hasAttribute('data-favorite-toggle')){await toggleAccountFavorite(target.dataset.favoriteToggle);return;}
    if(target.dataset.favoriteChoice){chooseFavoriteAccount(target.closest('[data-account-picker]'),target.dataset.favoriteChoice);return;}
    if(target.dataset.reviewOpen){$('#review-dialog').close();state.queueFilter='all';await selectReceipt(target.dataset.reviewOpen);return;}
    if(target.dataset.zoomReceipt){openZoom(target.dataset.zoomReceipt,target);return;}
    if(target.dataset.ocrReceipt){const details=target.closest('details');if(!details.open&&!details.dataset.loaded){const r=await api('/receipts/'+target.dataset.ocrReceipt);if(details.isConnected){details.querySelector('pre').textContent=r.ocr?.text||'読み取った本文がありません。原本を確認してください。';details.dataset.loaded='1';}}return;}
    if(target.dataset.receipt){await selectReceipt(target.dataset.receipt);return;}
    if(target.dataset.openReceipt){state.queueFilter='all';await selectReceipt(target.dataset.openReceipt);return;}
    if(target.dataset.fixExport){state.queueFilter='all';if(await selectReceipt(target.dataset.fixExport)){$('.tax-details')?.setAttribute('open','');$('[name="export_tax"]')?.focus();}return;}
    if(target.dataset.sourceTab){state.sourceTab=target.dataset.sourceTab;if(state.sourceTab==='history')state.history=(await api(`/receipts/${state.active}/history`)).history;renderDetail();return;}
    if(target.dataset.removeRule){await api('/rules/remove',{method:'POST',body:JSON.stringify({merchant:target.dataset.removeRule})});state.config=await api('/config');render();toast('科目のルールを解除しました。');return;}
    const action=target.dataset.action;
    if(action==='cancel-account-change'){if(!accountEdit?.saving)$('#account-change-dialog').close();return;}
    if(action==='reload-account-change'){await reloadAccountChange();return;}
    if(action==='back-to-ledger'){returnFromDetail();return;}
    if(action==='save-export-map'){
      const names={};
      for(const line of $('#export-account-map').value.split('\n').filter(v=>v.trim())){
        const index=line.indexOf('=');if(index<1)throw new Error('科目の対応は「元の科目=出力先の科目」の形式で入力してください。');
        names[line.slice(0,index).trim()]=line.slice(index+1).trim();
      }
      const result=await api('/export-map',{method:'POST',body:JSON.stringify({profile:state.exportProfile,names})});
      (state.config.export_account_maps||={})[state.exportProfile]=result.names;updateExportPreview();toast('選択中の形式の科目名の対応を保存しました。');return;
    }
    if(action==='review-ledger'){openBatchReview();return;}
    if(action==='confirm-batch'){target.disabled=true;try{await reviewLedger();}finally{target.disabled=false;}return;}
    if(action==='retry-errors'){target.disabled=true;try{await retryErrors();}finally{target.disabled=false;}return;}
    if(action==='queue-prev'||action==='queue-next'){state.queuePage+=action==='queue-next'?1:-1;$('#queue-list').scrollTop=0;renderQueue();return;}
    if(action==='ledger-prev'||action==='ledger-next'){state.ledgerPage+=action==='ledger-next'?1:-1;updateLedgerTable(true);focusPager('#ledger-page-controls .pagination',action);return;}
    if(action==='results-prev'||action==='results-next'){state.resultsPage+=action==='results-next'?1:-1;renderResults($('#main-content'));focusPager('#main-content > .pagination',action);return;}
    if(action==='zoom-prev'||action==='zoom-next'){zoomPage+=action==='zoom-next'?1:-1;paintZoom();return;}
    if(action==='download-csv'){await downloadCsv();return;}
    if(action==='upload'||target.id==='upload-top'){RyoshuImport.open();return;}
    if(action==='exclude-selected'||action==='restore-selected'){target.disabled=true;try{await setSelectedStatus(action==='exclude-selected'?'exclude':'restore');}finally{target.disabled=false;}return;}
    if(action==='filter-batch-issues'||action==='clear-issue-filter'){state.batchOnlyIssues=action==='filter-batch-issues';state.ledgerFilter='all';state.ledgerSearch='';state.month='';state.ledgerPage=0;render();return;}
    if(action==='dismiss-batch-result'){state.batchReport=null;state.batchOnlyIssues=false;render();return;}
    if(action==='clear-selection'){state.selected.clear();render();return;}
    if(action==='confirm-next'){await save(true,true);return;}
    if(action==='save-draft'){await save(false);return;}
    if(action==='all-months'){state.month='';state.ledgerPage=0;refreshMonth();$('[data-action="all-months"]')?.focus({preventScroll:true});return;}
    if(action==='prev-page'||action==='next-page'){state.page+=action==='prev-page'?-1:1;renderSource();return;}
    if(action==='use-suggestion'){
      const body=captureForm();for(const key of ['date','merchant','total','description','account','tax_rate','tax_amount','invoice_number'])if(key in state.detail.suggestion)body.draft[key]=state.detail.suggestion[key];
      state.drafts.set(state.active,body);renderDetail();return;
    }
    if(['retry','exclude','restore'].includes(action)){
      if(state.drafts.has(state.active)){toast('先に変更を下書き保存してください。',true);return;}
      if(action==='retry'&&state.detail.status==='confirmed'){toast('確認済みです。「変更を保存」で未確認に戻すと、もう一度読み取れます。',true);return;}
      target.disabled=true;state.detail=await api(`/receipts/${state.active}/${action}`,{method:'POST',body:JSON.stringify({version:state.detail.version})});
      await loadReceipts();render();schedulePoll();toast(action==='retry'?'再読み取りを開始しました。入力済みの下書きは保持します。':action==='exclude'?'対象外にしました。いつでも戻せます。':'未確認に戻しました。');return;
    }
    if(action==='save-settings'){
      await api('/settings',{method:'POST',body:JSON.stringify({business_context:$('#business-context').value.trim(),review_before_ledger:$('#review-before-ledger').checked})});state.config=await api('/config');toast('設定を保存しました。');return;
    }
  }catch(error){target.disabled=false;toast(error.message,true);}
});
document.addEventListener('submit',event=>{if(event.target.id==='account-change-form'){event.preventDefault();saveAccountChange();}if(event.target.id==='review-form'){event.preventDefault();save(true);}if(event.target.id==='account-form'){event.preventDefault();addAccount(event.target);}});
document.addEventListener('input',event=>{if(event.target.id==='ledger-search'){state.ledgerSearch=event.target.value;state.ledgerPage=0;if(!event.isComposing&&!state.searchComposing){clearTimeout(searchTimer);searchTimer=setTimeout(()=>updateLedgerTable(true),180);}}if(event.target.closest('#review-form'))trackEdit();if(event.target.id==='catalog-search')renderCatalogRows(event.target.value);});
document.addEventListener('change',event=>{
  if(event.target.matches('[data-account-category]')){
    changeAccountCategory(event.target.closest('[data-account-picker]'));
    if(event.target.closest('#review-form'))state.reviewAccountCategories.set(state.active,event.target.value);
  }
  if(event.target.matches('[data-account-choice]')){updateAccountHelp(event.target.closest('[data-account-picker]'));if(event.target.closest('#review-form'))state.reviewAccountCategories.set(state.active,$('[data-account-category]',event.target.closest('[data-account-picker]')).value);}
  if(event.target.closest('#account-change-form')&&(event.target.matches('[data-account-category]')||event.target.matches('[data-account-choice]')))updateAccountChangeSave();
  if(event.target.id==='catalog-category'){state.catalogCategory=event.target.value;renderCatalogRows(state.catalogSearch);}
  if(event.target.id==='account-add-kind'){
    const field=$('#account-add-category'),selected=field.value;
    field.innerHTML=addAccountCategoryOptions(event.target.value,selected);
  }
  if(event.target.id==='results-pending'){state.resultsOnlyPending=event.target.checked;state.resultsPage=0;renderResults($('#main-content'));$('#results-pending')?.focus({preventScroll:true});}
  if(event.target.id==='zoom-scale'){const img=$('.zoom-image-wrap img');img.style.width=event.target.value==='fit'?'100%':Math.round(img.naturalWidth*Number(event.target.value)/100)+'px';img.style.maxWidth=event.target.value==='fit'?'100%':'none';}
  if(event.target.id==='export-profile'){rememberLedgerInputs();state.exportProfile=event.target.value;$('#export-panel').outerHTML=exportPanel();updateExportPreview();$('#export-profile')?.focus({preventScroll:true});}
  if(event.target.id==='export-new-only'){state.exportNewOnly=event.target.checked;updateExportPreview();}
  if(event.target.id==='export-kind'){state.exportKind=event.target.value;updateExportPreview();}
  if(event.target.id==='export-tax-mode'){state.exportTaxMode=event.target.value;api('/settings',{method:'POST',body:JSON.stringify({export_tax_mode:state.exportTaxMode})}).catch(error=>toast('今回の出力には選択を使いますが、初期値の保存に失敗しました：'+error.message,true));updateExportPreview();}
  if(event.target.classList.contains('queue-filter')){state.queueFilter=event.target.value;state.queuePage=0;state.queuePageAnchor=state.active;renderQueue();}
  if(event.target.id==='ledger-filter'){state.ledgerFilter=event.target.value;state.ledgerPage=0;updateLedgerTable(true);}
  if(event.target.dataset.ledgerSelect){const id=event.target.dataset.ledgerSelect,row=state.records.find(r=>r.id===id);if(event.target.checked&&row)state.selected.set(id,row.version);else state.selected.delete(id);}
  if(event.target.id==='select-ledger-all'){for(const el of $$('[data-ledger-select]')){el.checked=event.target.checked;const row=state.records.find(r=>r.id===el.dataset.ledgerSelect);if(el.checked&&row)state.selected.set(row.id,row.version);else if(row)state.selected.delete(row.id);}}
  if(event.target.id==='month-filter'){state.month=event.target.value;state.ledgerPage=0;refreshMonth();$('#month-filter')?.focus({preventScroll:true});}
  if(event.target.id==='review-before-ledger'){api('/settings',{method:'POST',body:JSON.stringify({review_before_ledger:event.target.checked})}).then(result=>{state.config.review_before_ledger=result.review_before_ledger;toast('表示順の設定を保存しました。');}).catch(error=>{event.target.checked=!event.target.checked;toast(error.message,true);});}
  if(event.target.id==='review-ack')$('#confirm-batch').disabled=!event.target.checked;
  if(event.target.dataset.ledgerSelect||event.target.id==='select-ledger-all'){const details=$('#batch-review-details');if(details){details.open=state.selected.size>0||!!state.batchReport;$('#batch-selection').textContent=`選択した明細をまとめて確認（${state.selected.size}件）`;}if(!state.selected.size&&state.ledgerNeedsRefresh)refreshLedgerDisplay();}
  if(event.target.closest('#review-form'))trackEdit();
});
document.addEventListener('keydown',event=>{if((event.key==='Enter'||event.key===' ')&&event.target.matches('.dropzone')){event.preventDefault();RyoshuImport.open();}});
window.addEventListener('beforeunload',event=>{if(state.drafts.size||RyoshuImport.busy()||(accountEdit?.row&&$('#quick-account')?.value!==(findAccount(accountEdit.row.draft.account)?.name||accountEdit.row.draft.account||''))){event.preventDefault();event.returnValue='';}});

async function init(){
  try{
    await RyoshuSecurity.prepare();
    const [config,health,receipts]=await Promise.all([api('/config'),api('/health'),api('/receipts')]);
    state.config=config;state.health=health;state.records=receipts.receipts;state.processingFetchedAt=Date.now();
    if(!config.limits?.max_split_pages){state.uiReady=false;$('.sidebar nav').inert=true;$('#upload-top').disabled=true;$$('.nav-item').forEach(el=>{if(el.tagName==='BUTTON')el.disabled=true;});$('#main-content').innerHTML='<section class="panel settings-card"><h2>アプリ本体を再起動してください</h2><p>画面とアプリ本体の版が一致しません。一度「停止.cmd」で終了し、「Ryoshu.cmd」から開き直してください。帳簿と原本は削除していません。</p></section>';return;}

    RyoshuImport.setup(config,{api,begin:beginImport,saved:uploaded,progress:renderProcessing,notice:toast,open:async id=>{state.queueFilter='all';await selectReceipt(id);}});
    state.exportTaxMode=config.export_tax_mode||'reviewed';state.view=['home','results','ledger','settings','accounts'].includes(location.hash.slice(1))?location.hash.slice(1):'home';state.exportKind=config.review_before_ledger?'expenses':'review';
    if(health.environment_label){$('#connection-banner').textContent=health.environment_label+'。実際の帳簿とは別のデータベースです。';$('#connection-banner').classList.remove('hidden');}
    $('#app-version').textContent=health.app_version;$('#engine-status').textContent=`Indra ${health.ocr_version} / Qwen3 4B`;
    if(!health.model_ready){$('#connection-banner').textContent=(health.environment_label?health.environment_label+'。 ':'')+(health.model_issue||'勘定科目の候補を作るAIに接続できません。Ollamaアプリを起動してください。初回準備がまだの場合は「初回セットアップ.cmd」を実行します。読み取れた本文を見ながら手入力することもできます。');$('#connection-banner').classList.remove('hidden');}
    const storage=config.storage_issues||{};
    if(storage.missing_originals||storage.missing_source_pdfs){const banner=$('#connection-banner');banner.textContent+=(banner.textContent?' ':'')+`不足している原本 ${storage.missing_originals||0}件・元PDF ${storage.missing_source_pdfs||0}件があります。帳簿は開けますが、原本を確認してください。同じ原本を取り込み直すと修復できます。`;banner.classList.remove('hidden');}
    const first=state.records.find(isPending)||state.records[0];if(first){state.active=first.id;state.detail=await api('/receipts/'+first.id);}
    state.uiReady=true;render();schedulePoll();
  }catch(error){$('#main-content').innerHTML=`<section class="panel settings-card"><h2>アプリに接続できませんでした</h2><p>${esc(error.message)}</p><button class="button" data-action="reload">再読み込み</button></section>`;}
}
document.addEventListener('click',event=>{if(event.target.closest('[data-action="reload"]'))location.reload();});
init();



document.addEventListener('compositionstart',event=>{if(event.target.id==='ledger-search'){state.searchComposing=true;clearTimeout(searchTimer);}});
document.addEventListener('compositionend',event=>{if(event.target.id==='ledger-search'){state.searchComposing=false;state.ledgerSearch=event.target.value;state.ledgerPage=0;updateLedgerTable(true);}});
