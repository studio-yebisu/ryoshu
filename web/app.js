'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const yen = value => value == null ? '—' : '¥' + Number(value).toLocaleString('ja-JP');
const localMonth = () => {const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;};
const statusNames = {queued:'順番待ち',processing:'読み取り中',review:'確認待ち',confirmed:'登録済み',excluded:'対象外',error:'要再確認'};
const state = {view:'inbox',config:null,catalog:null,health:null,records:[],active:null,detail:null,sourceTab:'image',page:0,history:null,queueFilter:'pending',month:localMonth(),drafts:new Map(),busy:false};
let toastTimer, pollTimer;
Object.assign(state,{exportProfile:'generic',exportKind:'expenses',exportTaxMode:'reviewed',exportRevision:0});

async function api(path, options = {}) {
  const response = await fetch('/api' + path, {...options, headers:{'X-Ledger-Request':'1', ...(options.body instanceof FormData ? {} : {'Content-Type':'application/json'}), ...options.headers}});
  if (!response.ok) {
    let result; try {result = await response.json();} catch {result = {};}
    let message = result.detail || '処理に失敗しました。もう一度お試しください。';
    if (Array.isArray(message)) message = message.map(e => typeof e === 'string' ? e : `${e.loc?.slice(-1)[0] || ''}: ${e.msg}`).join('\n');
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
  const rows = state.records.filter(r => r.status === 'confirmed' && r.draft.date.startsWith(month));
  return {count:rows.length, total:rows.reduce((v,r)=>v+r.business_amount,0), expenses:rows.filter(r=>r.account_kind!=='asset').reduce((v,r)=>v+r.business_amount,0), assets:rows.filter(r=>r.account_kind==='asset').reduce((v,r)=>v+r.business_amount,0)};
}

function stats(month = '') {
  const sum = summaryFor(month);
  return `<div class="summary-grid"><div class="stat-card"><div class="stat-label"><span class="stat-dot"></span>確認を待っている領収書</div><strong>${state.records.filter(isPending).length}<small>件</small></strong><div class="stat-caption">読み取り中・要再確認を含む</div></div><div class="stat-card featured"><div class="stat-label">登録済みの経費 ${month ? esc(month.replace('-','年')+'月') : '· 全期間'}</div><strong>${yen(sum.expenses)}</strong><div class="stat-caption">事業分・税込 / 資産候補を除く</div><span class="stat-icon">↗</span></div><div class="stat-card"><div class="stat-label">帳簿に登録した領収書</div><strong>${sum.count}<small>件</small></strong><div class="stat-caption">${sum.assets ? '資産候補 ' + yen(sum.assets) + ' を含む' : '確認した明細だけを集計'}</div></div></div>`;
}

function render() {
  $$('.nav-item').forEach(el => el.classList.toggle('active',el.dataset.view===state.view));
  $('#pending-count').textContent = state.records.filter(isPending).length;
  const titles = {inbox:['領収書の確認','読み取った内容を確かめて、経費帳へ。'],ledger:['経費帳','確認して登録した支出を、いつでも見返せます。'],accounts:['科目別の集計','何に、どれくらい使ったかを見渡す。'],settings:['設定・保存','事業のことと、このPCでの保存について。']};
  titles.catalog=['勘定科目マスター','科目・用途・確認ポイントを、このPCのデータベースで管理します。'];
  $('#page-title').textContent=titles[state.view][0]; $('#page-description').textContent=titles[state.view][1];
  const content = $('#main-content');
  if (state.view==='inbox') renderInbox(content);
  if (state.view==='ledger') renderLedger(content);
  if (state.view==='accounts') renderAccounts(content);
  if (state.view==='settings') renderSettings(content);
  if (state.view==='catalog') renderCatalog(content);
}

function renderInbox(content) {
  if (!state.records.length) {
    content.innerHTML=stats()+`<section class="panel empty-layout"><div class="dropzone" data-action="upload" role="button" tabindex="0" aria-label="領収書を取り込む"><div class="receipt-drawing" aria-hidden="true"></div><h2>最初の領収書を取り込む</h2><p>画像やPDFを、この場所にドラッグできます。</p><span class="button primary">ファイルを選ぶ</span><span class="format-note">JPG / PNG / WebP / PDF · 1ファイル20MBまで</span></div><div class="getting-started"><div class="section-kicker">A LITTLE LESS PAPERWORK</div><h2>ひと手間で、<br>毎日の経費をすっきり。</h2><div class="step"><span class="step-number">01</span><div><strong>領収書を取り込む</strong><p>日付・支払先・金額を読み取ります。<br>複数のファイルもまとめてどうぞ。</p></div></div><div class="step"><span class="step-number">02</span><div><strong>科目の候補を確認する</strong><p>AIの理由と原本を見比べて修正。<br>業務の用途と事業割合を添えます。</p></div></div><div class="step"><span class="step-number">03</span><div><strong>経費帳に登録する</strong><p>確認した内容を保存し、<br>経費帳や仕訳をCSVに出せます。</p></div></div></div></section><p class="privacy-note"><span class="status-dot"></span> 領収書の画像と内容は、外部サービスへ送信しません。</p>`;
    return;
  }
  content.innerHTML=stats()+`<div class="workspace"><section class="panel queue"><div class="queue-head"><h2>取り込んだ領収書</h2><span class="table-sub">${state.records.length}件</span></div><select class="queue-filter" aria-label="領収書を絞り込む"><option value="pending">確認待ち</option><option value="all">すべて</option><option value="confirmed">登録済み</option><option value="excluded">対象外</option></select><div class="queue-list" id="queue-list"></div></section><section class="panel review-panel" id="review-panel"></section></div><p class="inbox-note">1ファイルを1件の支出として読み取ります。複数の領収書が入ったPDFは、取り込み前に分割してください。</p>`;
  $('.queue-filter').value=state.queueFilter;
  renderQueue(); renderDetail();
}

function visibleRecords() {return state.records.filter(r => state.queueFilter==='all' || (state.queueFilter==='pending' ? isPending(r) : r.status===state.queueFilter));}
function renderQueue() {
  const el = $('#queue-list'); if (!el) return;
  const scroll=el.scrollTop, left=el.scrollLeft;
  el.innerHTML=visibleRecords().map(r=>`<button class="queue-item ${state.active===r.id?'selected':''}" data-receipt="${esc(r.id)}"><${r.preview_count?'img':'span'} class="receipt-thumb ${r.preview_count?'':'thumb-placeholder'}" ${r.preview_count?`src="/api/receipts/${r.id}/preview/0" alt=""`:'aria-hidden="true"'}>${r.preview_count?'':'▤</span>'}<div class="queue-item-info"><div class="queue-item-name">${esc(r.draft.merchant || r.original_name)}</div><div class="queue-item-meta">${esc(r.draft.date || '日付を確認')}</div><div class="queue-item-amount">${yen(r.draft.total)}</div><div class="queue-badge">${badge(r.status)}${state.drafts.has(r.id)?'<span class="unsaved"> · 未保存</span>':''}</div></div></button>`).join('') || '<div class="empty-queue">この条件の領収書はありません</div>';
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

function renderDetail() {
  const el=$('#review-panel'); if(!el)return;
  const r=state.detail;
  if(!r || r.id!==state.active){el.innerHTML='<div class="preview-empty">左の領収書を選んで、内容を確認できます。</div>';return;}
  const d=currentDraft(),s=r.suggestion;
  const name=r.draft.merchant || r.original_name;
  const top=`<div class="review-top"><div><h2>${esc(name)}</h2><div class="review-subtitle">${esc(r.original_name)} · ${esc(r.id.slice(0,8).toUpperCase())}</div></div>${badge(r.status)}</div>`;
  if(busyRecord(r)) {el.innerHTML=top+`<div class="processing-box"><span class="spinner"></span><h3>${r.stage==='model'?'購入内容と科目の候補を確認中':'領収書の文字を読み取り中'}</h3><p>${r.stage==='model'?'ローカルAIが日付・金額・科目を整理しています。':'順番に処理します。そのままお待ちください。'}<br>完了すると、この画面に候補が表示されます。</p></div>`;return;}
  el.innerHTML=top+`<div class="review-body"><div class="source-panel"><div class="source-tabs"><button class="source-tab ${state.sourceTab==='image'?'active':''}" data-source-tab="image">原本</button><button class="source-tab ${state.sourceTab==='ocr'?'active':''}" data-source-tab="ocr">OCR本文</button><button class="source-tab ${state.sourceTab==='history'?'active':''}" data-source-tab="history">変更履歴</button></div><div id="source-content"></div></div><div class="form-panel">${r.status==='excluded'?'<div class="warning-list">この領収書は集計の対象外です。保存すると確認待ちに戻ります。</div>':''}${r.error?`<div class="error-box">${esc(r.error)}</div>`:''}${s?`<div class="suggestion-card"><div class="suggestion-label"><span>${s.origin==='merchant_rule'?'支払先ルールの候補':s.model?(s.retrieval?.length?'品目辞書・AIの候補':'AIによる科目候補'):'手入力で確認'}</span><span>${s.model?(s.account?'AI自己評価：'+({high:'高',medium:'中',low:'低'}[s.raw_model_candidate?.confidence||s.confidence]||'低'):'要確認'):''}</span></div><h3>${esc(s.account||'用途・科目を確認してください')}</h3><p>${esc(s.reason)}</p>${s.review_questions?.length?`<details class="candidate-questions"><summary>この候補の確認ポイント</summary><p>${s.review_questions.map(esc).join(' / ')}</p></details>`:''}${s.model?'<button class="text-button" data-action="use-suggestion">AI候補を入力欄へ戻す</button>':''}</div>`:''}${s?.warnings?.length?`<div class="warning-list">${s.warnings.map(w=>`<p>· ${esc(w)}</p>`).join('')}</div>`:''}<form id="review-form" novalidate><div class="form-row">${field('date','取引日','date',{required:true})}${field('total','領収書の合計（税込・円）','number',{required:true,placeholder:'原本の合計金額'})}</div>${field('merchant','支払先','text',{required:true,placeholder:'店舗名・取引先名',maxlength:200})}${field('account','勘定科目','select',{required:true,items:[['','科目を選択してください'],...state.config.accounts.map(a=>[a.name,a.name])]})}${field('description','購入内容・摘要','text',{placeholder:'例：制作資料の書籍',maxlength:1000})}${field('purpose','業務での用途','textarea',{required:true,placeholder:'例：案件Aの調査資料として使用'})}<div class="form-row">${field('business_percent','事業に使った割合（%）','number',{required:true,placeholder:'例：100',hint:'私用との共用は根拠のある割合を入力'})}${field('payment','支払方法','select',{required:true,items:[['','支払元を選択'],...Object.entries(state.config.payments).map(([k,v])=>[k,v.label])]})}</div><div class="business-result"><span>帳簿に入れる事業分</span><strong id="business-amount">${d.total!=null&&d.business_percent!=null?yen(Math.round(d.total*d.business_percent/100)):'—'}</strong></div><details class="tax-details"><summary>領収書の税額・登録番号（任意）</summary>${field('tax_rate','記載されている税率','select',{items:Object.entries(state.config.tax_rates)})}${field('tax_amount','記載消費税（円）','number',{placeholder:'書かれている場合のみ'})}${field('invoice_number','記載登録番号','text',{placeholder:'T と13桁の数字',maxlength:14,hint:'番号の記録のみ。登録事業者の照会は行いません。'})}</details><label class="checkbox-line"><input name="remember_account" type="checkbox" ${state.drafts.get(r.id)?.remember_account?'checked':''}>この支払先の科目を次回の候補にする</label><div id="form-errors" class="error-box hidden" role="alert"></div><div class="form-actions"><button class="button" type="button" data-action="save-draft">下書き保存</button><button class="button primary" type="submit" ${s?.blocking_reasons?.length?'disabled title="この取引は別途の仕訳確認が必要です"':''}>${r.status==='confirmed'?'変更を確認して登録':'確認して帳簿に登録'} <span>✓</span></button></div><div id="save-status" class="${state.drafts.has(r.id)?'unsaved':'field-hint'}">${state.drafts.has(r.id)?'変更はまだ保存していません。':r.status==='confirmed'?'登録済み。下書き保存すると確認待ちに戻ります。':'原本・科目・用途を確かめてから登録してください。'}</div><div class="journal-preview" id="journal-preview"></div><div class="secondary-actions"><button class="text-button" type="button" data-action="retry">再読取・AIを再実行</button><button class="text-button" type="button" data-action="${r.status==='excluded'?'restore':'exclude'}">${r.status==='excluded'?'確認待ちへ戻す':'経費の対象外にする'}</button></div></form></div></div>`;
  $('.tax-details',el)?.insertAdjacentHTML('beforeend',field('export_tax','会計ソフトへ出す消費税区分','select',{items:Object.entries(state.config.export_tax_choices||{'':'未確認'}),hint:'会計ソフト向けCSVで使います。税率やT番号だけでは自動選択しません。税額は取込先で計算します。'}));
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
    el.innerHTML=`<div class="preview-toolbar"><span class="page-pager"><button data-action="prev-page" aria-label="前のページ" ${state.page===0?'disabled':''}>‹</button>${count?state.page+1:0} / ${count}<button data-action="next-page" aria-label="次のページ" ${state.page>=count-1?'disabled':''}>›</button></span><a href="/api/receipts/${r.id}/original">原本を保存 ↗</a></div>${count?`<div class="preview-wrap"><img src="/api/receipts/${r.id}/preview/${state.page}" alt="取り込んだ領収書の原本プレビュー"></div>`:'<div class="preview-empty">プレビューを作成できませんでした。<br>原本ファイルを開いて確認できます。</div>'}<div class="source-caption">${r.ocr?`Indra OCR ${esc(r.ocr.version)} · ${r.ocr.page_count}ページ · OCR ${r.ocr.seconds}秒${r.suggestion?.seconds?' / AI '+r.suggestion.seconds+'秒':''}<br>`:''}原本のSHA-256：${esc(r.sha256.slice(0,16))}…</div>`;
  }else if(state.sourceTab==='ocr') {
    const evidence=r.suggestion?.evidence||{};
    el.innerHTML=`${Object.keys(evidence).length?`<div class="source-caption">候補の根拠</div><div class="journal-preview">${Object.entries(evidence).map(([k,v])=>`<p><strong>${esc({date:'日付',merchant:'支払先',total:'金額'}[k])}</strong>：${esc(v)}</p>`).join('')}</div>`:''}<pre class="ocr-text">${esc(r.ocr?.text||'OCR本文がありません。原本を見ながら入力できます。')}</pre><div class="source-caption">OCRの誤読は原本を見ながら入力欄で修正してください。</div>`;
  }else {
    const names={import:'原本を取り込み',analysis:'OCR・候補を取得',save:'下書きを保存',confirm:'帳簿に登録',exclude:'対象外に変更',restore:'確認待ちへ移動',retry:'再読取を開始'};
    el.innerHTML=state.history ? state.history.map(h=>`<div class="history-item"><time>${esc(new Date(h.at).toLocaleString('ja-JP'))}</time><strong>${esc(names[h.action]||h.action)}</strong>${h.detail?.after?`<details><summary>保存した内容</summary><pre>${esc(JSON.stringify(h.detail.after,null,2))}</pre></details>`:''}</div>`).join(''):'<div class="preview-empty">履歴を読み込み中…</div>';
  }
}

function monthControl() {return `<div class="toolbar-group"><input class="filter-input" id="month-filter" type="month" value="${esc(state.month)}" aria-label="集計する月"><button class="button small ${!state.month?'primary':''}" data-action="all-months">全期間</button></div>`;}
function renderLedger(content) {
  const rows=state.records.filter(r=>r.status==='confirmed'&&r.draft.date.startsWith(state.month)).sort((a,b)=>a.draft.date.localeCompare(b.draft.date));
  content.innerHTML=stats(state.month)+`<section class="panel"><div class="table-toolbar">${monthControl()}<span class="table-sub">原本と確認して登録した支出</span></div><div class="table-scroll"><table><thead><tr><th>取引日</th><th>支払先 / 摘要</th><th>勘定科目</th><th class="num">領収書合計</th><th class="num">事業割合</th><th class="num">事業分金額</th><th></th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.draft.date)}</td><td><div class="table-main">${esc(r.draft.merchant)}</div><div class="table-sub">${esc(r.draft.description||r.draft.purpose)}</div></td><td><span class="account-chip">${esc(r.draft.account)}</span></td><td class="num">${yen(r.draft.total)}</td><td class="num">${r.draft.business_percent}%</td><td class="num table-main">${yen(r.business_amount)}</td><td><button class="text-button" data-open-receipt="${r.id}">詳細 ↗</button></td></tr>`).join('')||'<tr><td colspan="7" class="empty-table">この期間の登録はまだありません。領収書を確認すると、ここに並びます。</td></tr>'}</tbody></table></div></section><p class="ledger-note">出力は税込の経費記録と経費仕訳です。カードは未払金として記録し、引落・売上・減価償却の仕訳は別途行ってください。<br>税率・登録番号は領収書の記載を保存します。消費税の申告計算・仕入税額控除の判定は行いません。</p>`;
  content.insertAdjacentHTML('afterbegin',exportPanel());
  updateExportPreview();
}

function exportPanel(){
  const profiles=state.config.export_profiles||{generic:{label:'汎用CSV'}};
  const native=state.exportProfile!=='generic';
  return `<section class="panel export-panel" id="export-panel"><div class="export-heading"><div><span class="section-kicker">EXPORT</span><h2>帳簿をCSVで持ち出す</h2><p>期間を選び、出力内容を確かめて保存できます。</p></div><span class="export-local">このPCで作成</span></div><div class="export-controls"><label class="field"><span class="field-label">取り込み先</span><select id="export-profile">${Object.entries(profiles).map(([k,v])=>`<option value="${esc(k)}" ${k===state.exportProfile?'selected':''}>${esc(v.label)}</option>`).join('')}</select></label>${native?`<label class="field"><span class="field-label">消費税の扱い</span><select id="export-tax-mode"><option value="reviewed" ${state.exportTaxMode==='reviewed'?'selected':''}>各領収書で確認した区分を使う</option><option value="exempt" ${state.exportTaxMode==='exempt'?'selected':''}>免税事業者として全行を対象外にする</option></select></label>`:`<label class="field"><span class="field-label">出力する内容</span><select id="export-kind"><option value="expenses" ${state.exportKind==='expenses'?'selected':''}>確定済みの経費帳</option><option value="journal" ${state.exportKind==='journal'?'selected':''}>確定済みの仕訳</option><option value="review" ${state.exportKind==='review'?'selected':''}>OCR候補・確認待ちを含む一覧</option></select></label>`}<div class="export-save"><button class="button primary" data-action="download-csv" id="download-csv" disabled>CSVを保存 ↓</button></div></div><p class="field-hint">${esc(profiles[state.exportProfile]?.route||'')} · ${esc(profiles[state.exportProfile]?.encoding||'UTF-8 BOM')}</p>${exportMapPanel()}<div id="export-result" aria-live="polite"><p class="ledger-note">出力内容を確認しています…</p></div></section>`;
}

function exportMapPanel(){
  if(state.exportProfile==='generic')return '';
  const mapping=state.config.export_account_maps?.[state.exportProfile]||{};
  return `<details class="export-sample"><summary>科目の出力名を合わせる（任意）</summary><p class="ledger-note">取り込み先で使っている科目名に合わせられます。1行に「元の科目=出力先の科目」を書いてください。元の帳簿は保持します。</p><label class="field"><span class="field-label">科目の対応</span><textarea id="export-account-map" placeholder="消耗品費=事務用品費">${esc(Object.entries(mapping).map(([k,v])=>k+'='+v).join('\n'))}</textarea></label><button class="button small" data-action="save-export-map">出力名を保存</button></details>`;
}

function exportQuery(){
  return new URLSearchParams({profile:state.exportProfile,kind:state.exportProfile==='generic'?state.exportKind:'journal',month:state.month,tax_mode:state.exportTaxMode});
}

async function updateExportPreview(){
  const revision=++state.exportRevision;
  try{
    const result=await api('/export-preview?'+exportQuery());
    if(revision!==state.exportRevision||!$('#export-result'))return;
    $('#download-csv').disabled=!result.ready||!result.receipt_count;
    $('#export-result').innerHTML=`<div class="export-totals"><strong>${result.receipt_count}件 / ${result.row_count}行</strong>${state.exportProfile!=='generic'?`<span>借方 ${yen(result.debit_total)} / 貸方 ${yen(result.credit_total)}</span>`:''}<span>${state.month?esc(state.month):'全期間'}</span></div>${result.issues.length?`<div class="error-box"><strong>出力前に${result.issues.length}点の確認が必要です</strong>${result.issues.map(v=>`<p><button class="text-button" data-fix-export="${esc(v.id)}">${esc(v.file)} ↗</button> ${esc(v.message)}</p>`).join('')}</div>`:''}${result.warnings.map(v=>`<p class="ledger-note">${esc(v)}</p>`).join('')}${result.rows.length?`<details class="export-sample"><summary>CSVの先頭${result.rows.length}行を確認する</summary><div class="table-scroll"><table><thead><tr>${result.columns.map(v=>`<th>${esc(v)}</th>`).join('')}</tr></thead><tbody>${result.rows.map(row=>`<tr>${row.map(v=>`<td>${esc(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`:'<p class="ledger-note">この期間に出力できるデータはありません。</p>'}`;
  }catch(error){if(revision===state.exportRevision&&$('#export-result'))$('#export-result').innerHTML=`<div class="error-box">${esc(error.message)}</div>`;}
}

async function downloadCsv(){
  const query=exportQuery(), kind=query.get('kind');
  const response=await fetch(`/api/export/${kind}?${query}`);
  if(!response.ok){const result=await response.json();throw new Error(result.detail||'CSVを作成できませんでした');}
  const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
  link.href=url;link.download=`${state.exportProfile}-${kind}-${state.month||'all'}.csv`;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
  toast('CSVを保存しました。取込先で件数と金額を確認してください。');
}

function renderAccounts(content) {
  const totals=new Map();
  state.records.filter(r=>r.status==='confirmed'&&r.draft.date.startsWith(state.month)).forEach(r=>{const v=totals.get(r.draft.account)||{amount:0,count:0};v.amount+=r.business_amount;v.count++;totals.set(r.draft.account,v);});
  const rows=[...totals].sort((a,b)=>b[1].amount-a[1].amount),max=rows[0]?.[1].amount||1;
  content.innerHTML=stats(state.month)+`<div class="account-grid"><section class="panel"><div class="table-toolbar">${monthControl()}<span class="table-sub">事業分・税込</span></div><div class="category-chart">${rows.map(([name,v])=>`<div class="category-row"><div class="category-top"><span>${esc(name)}${state.config.accounts.some(a=>a.name===name&&a.kind==='asset')?'（資産）':''}</span><strong>${yen(v.amount)}</strong></div><div class="category-bar"><span style="width:${v.amount/max*100}%"></span></div><div class="category-count">${v.count}件</div></div>`).join('')||'<div class="empty-table">登録すると、科目ごとの集計が見られます。</div>'}</div></section><section class="panel notes-panel"><div class="section-kicker">A CLEARER VIEW</div><h3>事業で使った分を、科目ごとに。</h3><p>家事按分した明細は、入力した事業割合で計算します。1円未満は四捨五入します。</p><p>資産の科目は経費の合計から分けています。減価償却額は計算しません。</p><p>AIの科目は提案です。支払先が同じでも購入内容が変わることがあるため、明細ごとに確認してください。</p><a class="text-button" href="https://www.nta.go.jp/taxes/shiraberu/taxanswer/shotoku/2210.htm" target="_blank" rel="noreferrer">必要経費について（国税庁） ↗</a></section></div>`;
}

function renderSettings(content) {
  const h=state.health,c=state.config;
  content.innerHTML=`<div class="settings-layout"><div><section class="panel settings-card"><h2>どんなお仕事ですか？</h2><p>事業内容を添えると、購入内容と仕事の関係をAIが考える材料になります。取引先の秘密や個人情報は書かずに概要だけで十分です。</p><label><span class="field-label">事業の概要（任意）</span><textarea class="settings-textarea" id="business-context" maxlength="1500" placeholder="例：ゲーム制作・イラスト制作。資料書籍や制作ソフトを使用。">${esc(c.business_context)}</textarea></label><button class="button primary" data-action="save-settings">保存する</button><div class="field-hint">次回の読み取りから使います。既存の候補を更新するには再読取してください。</div></section><section class="panel settings-card"><h2>保存した支払先の科目</h2><p>登録時にチェックした支払先だけ、科目を次回の候補に使います。</p>${c.rules.map(r=>`<div class="rule-row"><span>${esc(r.merchant)}<small>${esc(r.account)}</small></span><button class="text-button" data-remove-rule="${esc(r.merchant)}">解除</button></div>`).join('')||'<p>まだルールはありません。</p>'}</section><section class="panel settings-card"><h2>バックアップ</h2><p>領収書の原本、OCR本文、AI候補、修正履歴、帳簿と設定をZIPにまとめます。モデル本体は含みません。</p><div class="storage-path">${esc(h?.data_dir||'')}</div><a class="button" href="/api/backup">バックアップを保存 ↓</a><p class="field-hint">復元方法は同梱のREADMEに記載しています。ZIPにも領収書の内容が含まれます。</p></section></div><div><section class="panel settings-card"><h2>ローカルの実行環境</h2><div class="system-row"><span>文字の読み取り</span><strong>Indra OCR ${esc(h?.ocr_version||'—')}</strong></div><div class="system-row"><span>内容の判断</span><strong>Qwen3 4B</strong></div><div class="system-row"><span>モデルの状態</span><strong>${h?.model_ready?'準備できています':'Ollama・モデルを確認してください'}</strong></div><div class="system-row"><span>実行デバイス</span><strong>CPU</strong></div><div class="system-row"><span>帳簿の保存</span><strong>SQLite / このPC内</strong></div><div class="system-row"><span>アプリ</span><strong>${esc(h?.app_version||'0.3.0')}</strong></div><p class="ledger-note">読み取り時は画像も本文も外部へ送りません。初回セットアップでOCRとAIのモデルをダウンロードします。</p></section><section class="panel settings-card"><h2>この初版でできること</h2><p>個人事業主の支出の整理、科目候補の確認、税込の経費帳・経費仕訳CSVの作成が対象です。</p><p>確定申告書の作成、売上・口座照合、固定資産台帳、消費税の計算は含みません。電子帳簿保存法への対応を認証・保証する製品ではありません。</p><p>科目の用途や経費になるかは、業務の実態に合わせて確認して登録してください。</p></section></div></div>`;
  const samples=state.config.samples||{count:100,description:'架空の領収書で取込を試せます。'};
  $('.settings-layout > div',content)?.insertAdjacentHTML('beforeend',`<section class="panel settings-card"><h2>${samples.count}種類の架空領収書</h2><p><a href="/evidence/">テスト画像・期待値・100件の結果を見る ↗</a></p><p>${esc(samples.description)} 実際の取引ではありません。</p><div class="sample-actions">${samples.count===100?'<a class="button" href="/samples/receipts-100-v2/gallery.html">100パターンを一覧で見る ↗</a>':''}<a class="button" href="/api/samples/zip">個別PDF ${samples.count}件を保存 ↓</a><a class="button" href="/api/samples/review">${samples.count}ページの合本を見る ↗</a></div><p class="field-hint">アプリへの取込にはZIP内の個別PDFを使います。50件ずつ取り込めます。合本は閲覧用です。期待値・業務背景・作成モデルの記録を別添しています。</p></section>`);
}

const kindLabels={expense:'経費',asset:'資産',cash:'支払元',bank:'支払元',liability:'負債',equity:'事業主勘定'};
function renderCatalog(content){
  const catalog=state.catalog;
  if(!catalog){content.innerHTML='<section class="panel settings-card">マスターを読み込んでいます…</section>';return;}
  const accounts=catalog.accounts;
  content.innerHTML=`<div class="summary-grid"><div class="stat-card"><div class="stat-label">登録されている科目</div><strong>${accounts.length}<small>科目</small></strong><div class="stat-caption">経費・資産・支払元・事業主勘定</div></div><div class="stat-card featured"><div class="stat-label">確認条件のデータ</div><strong>${catalog.policies.length}<small>種類</small></strong><div class="stat-caption">飲食・返金・家事按分・資産など</div></div><div class="stat-card"><div class="stat-label">保存と参照</div><strong class="catalog-db-label">SQLite</strong><div class="stat-caption">画面・AI候補・仕訳が同じマスターを参照</div></div></div><section class="panel"><div class="table-toolbar"><div class="toolbar-group"><input class="filter-input" id="catalog-search" type="search" placeholder="科目名・別名・用途で検索" aria-label="科目を検索"><span class="table-sub" id="catalog-count"></span></div><span class="table-sub">DBスキーマ ${catalog.schema_version} / 初期データ ${esc(catalog.seed_version)}</span></div><div class="table-scroll catalog-scroll"><table class="catalog-table"><thead><tr><th>コード / 科目</th><th>区分</th><th>主な用途</th><th>確認すること・別名</th></tr></thead><tbody id="catalog-rows"></tbody></table></div></section><details class="panel settings-card catalog-add"><summary>自分用の勘定科目を追加する</summary><p class="field-hint">既存科目は保持します。独自の科目もAIの候補と登録先に使えます。</p><form id="account-form"><div class="form-row"><label class="field"><span class="field-label">科目名</span><input name="name" maxlength="100" required placeholder="例：制作サービス利用料"></label><label class="field"><span class="field-label">区分</span><select name="kind"><option value="expense">経費</option><option value="asset">資産</option></select></label></div><label class="field"><span class="field-label">用途・例</span><textarea name="description" maxlength="1000" placeholder="この科目で記録する支出の内容"></textarea></label><label class="field"><span class="field-label">別名（任意・カンマ区切り）</span><input name="aliases" placeholder="サービス利用料, ツール利用料"></label><label class="field"><span class="field-label">品目の手がかり（任意・カンマ区切り）</span><input name="keywords" placeholder="例：制作素材ライセンス, テクスチャ素材"></label><label class="field"><span class="field-label">確認ポイント（任意・1行に1項目）</span><textarea name="review_questions" placeholder="事業で使うサービスか\n当月分の支払か"></textarea></label><button class="button primary" type="submit">科目を追加</button></form></details><section class="panel settings-card"><h2>候補を確かめるための条件</h2><p>科目の提案に加え、次の条件を確認します。用途や経費算入の可否を領収書だけで確定しません。</p><div class="policy-grid">${catalog.policies.map(p=>`<article class="policy-card"><div class="suggestion-label"><span>${esc(p.code)}</span><span>${p.action==='block'?'通常の経費登録を停止':p.action==='hold'?'科目を保留':'確認事項を表示'}</span></div><h3>${esc(p.title)}</h3><p>${esc(p.message)}</p></article>`).join('')}</div><div class="catalog-sources"><span class="field-label">考え方の参照資料</span>${catalog.sources.map(s=>`<p><a href="${esc(s.url)}" target="_blank" rel="noreferrer">${esc(s.title)} ↗</a><span class="table-sub"> · 確認 ${esc(s.checked_at)}</span></p>`).join('')}<p class="field-hint">科目名や用途の例はアプリで編集した初期データです。税務上の取扱いを一律に確定する表ではありません。</p></div></section>`;
  renderCatalogRows('');
}

function renderCatalogRows(query){
  const el=$('#catalog-rows');if(!el)return;
  const normalized=String(query).normalize('NFKC').toLocaleLowerCase();
  const rows=state.catalog.accounts.filter(a=>[a.name,a.code,a.description,...a.aliases,...(a.hints?.any||[])].join(' ').normalize('NFKC').toLocaleLowerCase().includes(normalized));
  $('#catalog-count').textContent=`${rows.length}科目`;
  el.innerHTML=rows.map(a=>`<tr><td><div class="table-sub">${esc(a.code)}${a.seeded?'':' / 自分の科目'}</div><strong>${esc(a.name)}</strong></td><td><span class="account-chip">${esc(kindLabels[a.kind]||a.kind)}</span></td><td class="catalog-description">${esc(a.description)}</td><td class="catalog-description">${a.review_questions.map(q=>`<div>· ${esc(q)}</div>`).join('')||'<span class="table-sub">支払元・仕訳の相手科目として使用</span>'}${a.hints?.any?.length?`<div class="field-hint">品目の手がかり：${esc(a.hints.any.join(' / '))}</div>`:''}${a.aliases.length?`<div class="table-sub">別名：${esc(a.aliases.join(' / '))}</div>`:''}</td></tr>`).join('')||'<tr><td colspan="4" class="empty-table">一致する科目はありません</td></tr>';
}

async function addAccount(form){
  const data=Object.fromEntries(new FormData(form));
  const button=$('button[type="submit"]',form);button.disabled=true;
  try{
    await api('/accounts',{method:'POST',body:JSON.stringify({name:data.name.trim(),kind:data.kind,description:data.description.trim(),aliases:data.aliases.split(/[,、\n]/).map(v=>v.trim()).filter(Boolean),keywords:(data.keywords||'').split(/[,、\n]/).map(v=>v.trim()).filter(Boolean),review_questions:data.review_questions.split('\n').map(v=>v.trim()).filter(Boolean)})});
    [state.catalog,state.config]=await Promise.all([api('/catalog'),api('/config')]);render();toast('勘定科目をデータベースに追加しました。');
  }catch(error){toast(error.message,true);}finally{button.disabled=false;}
}

function captureForm() {
  const form=$('#review-form'); if(!form||!state.detail)return null;
  const values=Object.fromEntries(new FormData(form));
  const draft={...currentDraft()};
  for(const key of new Set([...Object.keys(state.detail.draft),'export_tax'])) if(key in values) draft[key]=['total','tax_amount','business_percent'].includes(key)?(values[key]===''?null:Number(values[key])):values[key].trim();
  return {version:state.drafts.get(state.active)?.version||state.detail.version,draft,remember_account:values.remember_account==='on'};
}
function trackEdit() {
  const body=captureForm();if(!body)return;
  state.drafts.set(state.active,body);$('#save-status').textContent='変更はまだ保存していません。';$('#save-status').className='unsaved';
  const d=body.draft;$('#business-amount').textContent=d.total!=null&&d.business_percent!=null?yen(Math.round(d.total*d.business_percent/100)):'—';
  renderJournal(d);renderQueue();
}

async function loadReceipts() {const result=await api('/receipts');state.records=result.receipts;}
async function selectReceipt(id) {
  state.active=id;state.detail=await api('/receipts/'+id);state.sourceTab='image';state.page=0;state.history=null;
  if(state.view!=='inbox')state.view='inbox';render();
}
async function save(confirm) {
  if(state.busy)return;
  const body=captureForm();if(!body)return;
  state.busy=true;const buttons=$$('#review-form button');buttons.forEach(b=>b.disabled=true);
  try {
    const result=await api(`/receipts/${state.active}/save`,{method:'POST',body:JSON.stringify({...body,confirm})});
    state.detail=result;state.drafts.delete(state.active);await loadReceipts();
    if(body.remember_account&&confirm)state.config=await api('/config');
    render();toast(confirm?'経費帳に登録しました。':'下書きを保存しました。');
  }catch(error){const box=$('#form-errors');if(box){box.textContent=error.message;box.classList.remove('hidden');box.scrollIntoView({block:'nearest',behavior:'smooth'});}else toast(error.message,true);}
  finally{state.busy=false;buttons.forEach(b=>b.disabled=false);}
}

async function upload(files) {
  if(!files?.length)return;
  const limit=state.config?.limits?.max_files||50;
  if(files.length>limit){toast(`一度に取り込めるのは${limit}ファイルまでです。`,true);return;}
  const form=new FormData();for(const file of files)form.append('files',file);
  $('#upload-top').disabled=true;toast('領収書を取り込んでいます…');
  try {
    const result=await api('/receipts',{method:'POST',body:form});await loadReceipts();
    const newCount=result.imported.filter(r=>!r.duplicate).length,duplicates=result.imported.length-newCount;
    if(result.imported.length){state.queueFilter='all';await selectReceipt(result.imported[0].id);}else render();
    toast([newCount?`${newCount}件を取り込みました。順番に読み取ります。`:'',duplicates?`${duplicates}件は同じ原本が保存されています。`:'',...result.errors.map(e=>`${e.name}：${e.message}`)].filter(Boolean).join('\n'),result.errors.length>0);
    schedulePoll();
  }catch(error){toast(error.message,true);}finally{$('#upload-top').disabled=false;$('#file-input').value='';}
}

function schedulePoll(){clearTimeout(pollTimer);if(state.records.some(busyRecord))pollTimer=setTimeout(poll,2200);}
async function poll(){
  try{await loadReceipts();$('#pending-count').textContent=state.records.filter(isPending).length;
    const updated=state.records.find(r=>r.id===state.active);
    if(updated&&(busyRecord(state.detail)||updated.version!==state.detail?.version)&&!state.drafts.has(state.active)){state.detail=await api('/receipts/'+state.active);if(state.view==='inbox')render();}
    else if(state.view==='inbox')renderQueue();
  }catch(error){toast('ローカルアプリとの接続を確認してください。',true);}
  schedulePoll();
}

document.addEventListener('click',async event=>{
  const target=event.target.closest('button,a,[data-action]');if(!target)return;
  try{
    if(target.dataset.view){if(target.dataset.view==='catalog')state.catalog=await api('/catalog');state.view=target.dataset.view;render();return;}
    if(target.dataset.receipt){await selectReceipt(target.dataset.receipt);return;}
    if(target.dataset.openReceipt){state.queueFilter='all';await selectReceipt(target.dataset.openReceipt);return;}
    if(target.dataset.fixExport){state.queueFilter='all';await selectReceipt(target.dataset.fixExport);$('.tax-details').open=true;$('[name="export_tax"]').focus();return;}
    if(target.dataset.sourceTab){state.sourceTab=target.dataset.sourceTab;if(state.sourceTab==='history')state.history=(await api(`/receipts/${state.active}/history`)).history;renderDetail();return;}
    if(target.dataset.removeRule){await api('/rules/remove',{method:'POST',body:JSON.stringify({merchant:target.dataset.removeRule})});state.config=await api('/config');render();toast('科目のルールを解除しました。');return;}
    const action=target.dataset.action;
    if(action==='save-export-map'){
      const names={};
      for(const line of $('#export-account-map').value.split('\n').filter(v=>v.trim())){
        const index=line.indexOf('=');if(index<1)throw new Error('科目の対応は「元の科目=出力先の科目」の形式で入力してください。');
        names[line.slice(0,index).trim()]=line.slice(index+1).trim();
      }
      const result=await api('/export-map',{method:'POST',body:JSON.stringify({profile:state.exportProfile,names})});
      (state.config.export_account_maps||={})[state.exportProfile]=result.names;updateExportPreview();toast('この形式の科目の出力名を保存しました。');return;
    }
    if(action==='download-csv'){await downloadCsv();return;}
    if(action==='upload'||target.id==='upload-top'){$('#file-input').click();return;}
    if(action==='save-draft'){await save(false);return;}
    if(action==='all-months'){state.month='';render();return;}
    if(action==='prev-page'||action==='next-page'){state.page+=action==='prev-page'?-1:1;renderSource();return;}
    if(action==='use-suggestion'){
      const body=captureForm();for(const key of ['date','merchant','total','description','account','tax_rate','tax_amount','invoice_number'])if(key in state.detail.suggestion)body.draft[key]=state.detail.suggestion[key];
      state.drafts.set(state.active,body);renderDetail();return;
    }
    if(['retry','exclude','restore'].includes(action)){
      if(state.drafts.has(state.active)){toast('先に変更を下書き保存してください。',true);return;}
      if(action==='retry'&&state.detail.status==='confirmed'){toast('登録済みです。下書き保存して確認待ちに戻すと再読取できます。',true);return;}
      target.disabled=true;state.detail=await api(`/receipts/${state.active}/${action}`,{method:'POST',body:JSON.stringify({version:state.detail.version})});
      await loadReceipts();render();schedulePoll();toast(action==='retry'?'再読取を開始しました。入力済みの下書きは保持します。':action==='exclude'?'対象外にしました。いつでも戻せます。':'確認待ちへ戻しました。');return;
    }
    if(action==='save-settings'){
      await api('/settings',{method:'POST',body:JSON.stringify({business_context:$('#business-context').value.trim()})});state.config=await api('/config');toast('事業の概要を保存しました。');return;
    }
  }catch(error){target.disabled=false;toast(error.message,true);}
});
document.addEventListener('submit',event=>{if(event.target.id==='review-form'){event.preventDefault();save(true);}if(event.target.id==='account-form'){event.preventDefault();addAccount(event.target);}});
document.addEventListener('input',event=>{if(event.target.closest('#review-form'))trackEdit();if(event.target.id==='catalog-search')renderCatalogRows(event.target.value);});
document.addEventListener('change',event=>{
  if(event.target.id==='export-profile'){state.exportProfile=event.target.value;$('#export-panel').outerHTML=exportPanel();updateExportPreview();}
  if(event.target.id==='export-kind'){state.exportKind=event.target.value;updateExportPreview();}
  if(event.target.id==='export-tax-mode'){state.exportTaxMode=event.target.value;updateExportPreview();}
  if(event.target.id==='file-input')upload(event.target.files);
  if(event.target.classList.contains('queue-filter')){state.queueFilter=event.target.value;renderQueue();}
  if(event.target.id==='month-filter'){state.month=event.target.value;render();}
  if(event.target.closest('#review-form'))trackEdit();
});
document.addEventListener('keydown',event=>{if((event.key==='Enter'||event.key===' ')&&event.target.matches('.dropzone')){event.preventDefault();$('#file-input').click();}});
let dragDepth=0;
document.addEventListener('dragenter',event=>{if(event.dataTransfer.types.includes('Files')){event.preventDefault();dragDepth++;$('#upload-overlay').classList.remove('hidden');}});
document.addEventListener('dragover',event=>{if(event.dataTransfer.types.includes('Files'))event.preventDefault();});
document.addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('#upload-overlay').classList.add('hidden');}});
document.addEventListener('drop',event=>{event.preventDefault();dragDepth=0;$('#upload-overlay').classList.add('hidden');upload(event.dataTransfer.files);});
window.addEventListener('beforeunload',event=>{if(state.drafts.size){event.preventDefault();event.returnValue='';}});

async function init(){
  try{
    const [config,health,receipts]=await Promise.all([api('/config'),api('/health'),api('/receipts')]);
    state.config=config;state.health=health;state.records=receipts.receipts;
    if(health.environment_label){$('#connection-banner').textContent=health.environment_label+'。実際の帳簿とは別のデータベースです。';$('#connection-banner').classList.remove('hidden');}
    $('#app-version').textContent=health.app_version;$('#engine-status').textContent=`Indra ${health.ocr_version} / Qwen3 4B`;
    if(!health.model_ready){$('#connection-banner').textContent='意味判断モデルの準備ができていません。Ollamaを起動するか、setup.ps1を実行してください。OCR本文からの手入力もできます。';$('#connection-banner').classList.remove('hidden');}
    const first=state.records.find(isPending)||state.records[0];if(first){state.active=first.id;state.detail=await api('/receipts/'+first.id);}
    render();schedulePoll();
  }catch(error){$('#main-content').innerHTML=`<section class="panel settings-card"><h2>アプリに接続できませんでした</h2><p>${esc(error.message)}</p><button class="button" data-action="reload">再読み込み</button></section>`;}
}
document.addEventListener('click',event=>{if(event.target.closest('[data-action="reload"]'))location.reload();});
init();
