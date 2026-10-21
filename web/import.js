'use strict';
// File handles stay in this browser tab. Originals accepted by the server persist locally.
window.RyoshuImport = (() => {
  const jobs = [];
  let config, handlers, running = false, paused = false, dragDepth = 0, nextId = 1, staged = [], resultsPage = 0, resultsFilter = 'all';
  const element = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const waiting = () => jobs.filter(j => j.status === 'waiting');
  const failed = () => jobs.filter(j => j.status === 'failed');
  const busy = () => running || waiting().length > 0;
  const labels = {waiting:'取り込み待ち',importing:'保存中',saved:'保存済み',duplicate:'重複のためスキップ',failed:'取り込み失敗',skipped:'対応していない形式'};

  function setup(settings, callbacks) {
    config = settings; handlers = callbacks;
    const dialog = element('import-dialog');
    dialog.innerHTML = `<form method="dialog" class="dialog-heading"><h2>領収書をまとめて取り込む</h2><button class="button small" aria-label="取り込み画面を閉じる">閉じる</button></form><div class="import-picker"><p>画像・PDFを複数選ぶか、フォルダーごと追加できます。</p><div class="import-file-buttons"><button class="button primary" id="choose-files">ファイルを選ぶ</button><button class="button" id="choose-folder">フォルダーを選ぶ</button></div><p class="field-hint">ここにファイルやフォルダーをドロップしても取り込めます。JPG・PNG・WebP・PDF、1ファイル20MBまで。</p></div><p id="import-selection" role="status"></p><fieldset class="pdf-options"><legend>PDFの取り込み方</legend><p class="field-hint">選んだPDFすべてに同じ取り込み方を使います。請求書の続きとレシートの束が混在する場合は、分けて選んでください。1ページだけのPDFは、どちらでも1件になります。</p><label class="checkbox-line"><input type="radio" name="pdf-mode" value="document">1ファイルを1つの取引にする</label><p class="field-hint">例：複数ページの請求書・明細付き領収書。1ファイル${config.limits.max_pages}ページまで。</p><label class="checkbox-line"><input type="radio" name="pdf-mode" value="pages">1ページずつ、別の領収書にする</label><p class="field-hint">例：ページごとにお店・日付が違う、スキャンしたレシートの束。${config.limits.max_split_pages}ページまで。分割前の元PDFも保存します。1ページ内に複数の領収書がある画像は分けられません。</p></fieldset><button class="button primary hidden" id="start-import" disabled>選択したファイルを取り込む</button><p class="ledger-note">取り込むと、未確認の帳簿を自動で作ります。内容の確認はあとからできます。</p>`;
    dialog.addEventListener('change', event => {if(event.target.name==='pdf-mode')element('start-import').disabled=!dialogMode();});
    element('start-import').addEventListener('click',()=>{const mode=dialogMode();if(!mode||!staged.length)return;const files=staged;staged=[];element('import-selection').textContent='';element('start-import').classList.add('hidden');queueFiles(files,mode);});
    dialog.addEventListener('close',()=>{if(staged.length){staged=[];element('import-selection').textContent='';element('start-import').classList.add('hidden');handlers.notice('選択したファイルの取り込みは開始していません。');}});
    element('choose-files').addEventListener('click', () => element('file-input').click());
    element('choose-folder').addEventListener('click', () => element('folder-input').click());
    for (const id of ['file-input', 'folder-input']) {
      element(id).addEventListener('change', event => {
        const mode = dialog.querySelector('[name="pdf-mode"]:checked')?.value;
        addFiles([...event.target.files], mode); event.target.value = '';
      });
    }
    element('import-progress').addEventListener('toggle',event=>{const details=event.target;if(details.tagName==='DETAILS'&&details.open&&element('import-progress').contains(details)&&!details.querySelector('.import-result-controls').childElementCount){details.querySelector('.import-results').innerHTML=resultsRows();details.querySelector('.import-result-controls').innerHTML=resultsControls();}},true);
    element('import-progress').addEventListener('change',event=>{if(event.target.id==='import-result-filter'){resultsFilter=event.target.value;resultsPage=0;renderProgress();element('import-result-filter')?.focus({preventScroll:true});}});
    element('import-progress').addEventListener('click', event => {
      const open=event.target.closest('[data-import-open]');
      if(open){Promise.resolve(handlers.open(open.dataset.importOpen)).catch(error=>handlers.notice(error.message,true));return;}
      const split=event.target.closest('[data-import-split]');
      if(split){const job=jobs.find(j=>j.key===Number(split.dataset.importSplit));if(job){job.mode='pages';job.status='waiting';job.message='';job.needsSplit=false;paused=false;pump();renderProgress();}return;}
      const action = event.target.closest('[data-import-action]')?.dataset.importAction;
      if(!action)return;
      if(action==='result-prev'||action==='result-next'){resultsPage+=action==='result-next'?1:-1;const list=element('import-progress').querySelector('.import-results');if(list)list.scrollTop=0;}
      if (action === 'pause') paused = true;
      if (action === 'resume') { paused = false; pump(); }
      if (action === 'retry') { for (const job of failed().filter(j=>!j.needsSplit)) {job.status = 'waiting'; job.message = '';} paused = false; pump(); }
      if (action === 'split-failed') {for(const job of failed().filter(j=>j.needsSplit)){job.mode='pages';job.status='waiting';job.message='';job.needsSplit=false;}paused=false;pump();}
      if (action === 'dismiss' && !busy()) { jobs.length = 0; }
      renderProgress();
    });
    document.addEventListener('dragenter', event => {
      if ([...event.dataTransfer.types].includes('Files')) {event.preventDefault();dragDepth++;element('upload-overlay').classList.remove('hidden');}
    });
    document.addEventListener('dragover', event => {if ([...event.dataTransfer.types].includes('Files')) event.preventDefault();});
    document.addEventListener('dragleave', () => {if (--dragDepth <= 0) {dragDepth = 0;element('upload-overlay').classList.add('hidden');}});
    document.addEventListener('drop', async event => {
      if (![...event.dataTransfer.types].includes('Files')) return;
      event.preventDefault();dragDepth = 0;element('upload-overlay').classList.add('hidden');
      const mode = dialog.open ? dialog.querySelector('[name="pdf-mode"]:checked')?.value : undefined;
      try {
        // Capture entries synchronously; browsers clear DataTransfer after this event.
        const entries = [...event.dataTransfer.items].map(item => item.webkitGetAsEntry?.()).filter(Boolean);
        const fallback = [...event.dataTransfer.files];
        const files = entries.length ? (await Promise.all(entries.map(readEntry))).flat() : fallback;
        if (!files.length) {handlers.notice('ファイルが見つかりませんでした。「フォルダーを選ぶ」から選択してください。', true);return;}
        addFiles(files, mode);
      } catch {handlers.notice('フォルダーを開けませんでした。「フォルダーを選ぶ」から選び直してください。', true);}
    });
  }

  async function readEntry(entry) {
    if (entry.isFile) return [await new Promise((resolve, reject) => entry.file(resolve, reject))];
    if (!entry.isDirectory) return [];
    const reader = entry.createReader(), children = [];
    for (;;) {
      const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
      if (!batch.length) break;
      children.push(...batch);
    }
    const files = [];
    for (const child of children) files.push(...await readEntry(child));
    return files;
  }

  function addFiles(files, mode) {
    if (!files.length) return;
    if(staged.length)files=[...staged,...files];
    if (files.length > 2000) {handlers.notice('一度に選べるのは2000ファイルまでです。領収書を入れたフォルダーを分けて追加してください。', true);return;}
    if(files.some(f=>/\.pdf$/i.test(f.name)||f.type==='application/pdf')){
      staged=files;
      element('import-selection').textContent=`${files.length}ファイルを選択しました。追加で選んだファイルも含まれています。PDFの取り込み方を選んで、下のボタンで開始してください。`;
      element('start-import').classList.remove('hidden');
      element('start-import').disabled=!dialogMode();
      if(!element('import-dialog').open)element('import-dialog').showModal();
      return;
    }
    queueFiles(files,mode||'document');
  }

  function dialogMode(){return element('import-dialog').querySelector('[name="pdf-mode"]:checked')?.value;}

  function queueFiles(files,mode){
    handlers.begin?.();
    for (const file of files) {
      const supported = /\.(pdf|jpe?g|png|webp)$/i.test(file.name) || /^(application\/pdf|image\/(jpeg|png|webp))$/.test(file.type);
      let message = !supported ? '対応していない形式です。JPG・PNG・WebP・PDFを選んでください。' : file.size === 0 ? '空のファイルです。原本を確認してください。' : file.size > config.limits.max_bytes ? '20MBを超えています。ファイルを分けるか、画像を縮小してください。' : '';
      jobs.push({key:nextId++, file, mode, status:!supported?'skipped':message?'failed':'waiting', message, rows:[], duplicate:0});
    }
    element('import-dialog').close();
    renderProgress(); pump();
  }

  async function pump() {
    if (running) return;
    running = true;
    while (!paused) {
      const job = waiting()[0]; if (!job) break;
      if (job.file.size === 0 || job.file.size > config.limits.max_bytes) {job.status = 'failed';job.message=job.file.size===0?'空のファイルです。原本を確認してください。':'20MBを超えています。ファイルを分けるか、画像を縮小してください。';renderProgress();continue;}
      job.status = 'importing';renderProgress();
      const form = new FormData();form.append('files', job.file);form.append('pdf_mode', job.mode);
      try {
        const result = await handlers.api('/receipts', {method:'POST', body:form});
        const saved = new Map(job.rows.map(row => [row.id, row]));
        for(const row of result.imported){if(!saved.has(row.id)||saved.get(row.id).duplicate)saved.set(row.id,row);}
        job.rows = [...saved.values()];job.duplicate = job.rows.filter(r => r.duplicate).length;
        job.message = result.errors.map(e => e.message).join(' / ');
        job.needsSplit = result.errors.some(e => e.code === 'split_required');
        job.status = result.errors.length ? 'failed' : result.imported.length && job.duplicate === result.imported.length ? 'duplicate' : 'saved';
        try {await handlers.saved(result);} catch {handlers.notice('原本は保存しました。一覧の更新に失敗したため、接続を確認して再読み込みしてください。', true);}
      } catch (error) {job.status = 'failed';job.message = error.message;}
      renderProgress();
    }
    running = false;renderProgress();
  }

  function resultJobs(){return jobs.filter(job=>resultsFilter==='all'||['failed','skipped'].includes(job.status));}
  function resultsRows(){const visible=resultJobs();resultsPage=Math.max(0,Math.min(resultsPage,Math.ceil(visible.length/100)-1));return visible.slice(resultsPage*100,(resultsPage+1)*100).map(j => `<li><span>${escape(j.file.webkitRelativePath || j.file.name)}</span><strong>${labels[j.status]}${j.rows.length>1?'（'+j.rows.length+'ページ）':''}</strong>${j.message?`<p>${escape(j.message)}</p>`:''}${j.needsSplit?`<button class="text-button" data-import-split="${j.key}">1ページずつで取り込み直す</button>`:''}${j.rows.length?`<div>${j.rows.slice(0,3).map(r=>`<button class="text-button" data-import-open="${r.id}">${r.duplicate?'登録済みの明細を開く':'明細を開く'}</button>`).join(' ')}</div>`:''}</li>`).join('');}
  function resultsControls(){const pages=Math.max(1,Math.ceil(resultJobs().length/100));resultsPage=Math.max(0,Math.min(resultsPage,pages-1));return `<div class="import-actions"><select id="import-result-filter" aria-label="取り込み結果を絞り込む"><option value="all" ${resultsFilter==='all'?'selected':''}>すべてのファイル</option><option value="problem" ${resultsFilter==='problem'?'selected':''}>失敗・対応していない形式</option></select><span>${resultsPage+1} / ${pages}ページ</span>${pages>1?`<button class="text-button" data-import-action="result-prev" ${resultsPage?'':'disabled'}>前へ</button><button class="text-button" data-import-action="result-next" ${resultsPage<pages-1?'':'disabled'}>次へ</button>`:''}</div>`;}

  function renderProgress() {
    const el = element('import-progress');
    if (!jobs.length) {el.classList.add('hidden');el.replaceChildren();resultsPage=0;resultsFilter='all';return;}
    el.classList.remove('hidden');
    const pending = waiting().length, active = jobs.filter(j => j.status === 'importing').length;
    const completed = jobs.length - pending - active;
    const saved = jobs.reduce((n,j) => n + j.rows.filter(r => !r.duplicate).length, 0);
    const duplicates = jobs.reduce((n,j) => n + j.duplicate, 0), skipped = jobs.filter(j => j.status === 'skipped').length;
    const splitCount=failed().filter(j=>j.needsSplit).length,retryCount=failed().length-splitCount;
    const opened = el.querySelector('details')?.open;
    const scroll=el.querySelector('.import-results')?.scrollTop||0;
    const focused=document.activeElement,focusAction=el.contains(focused)?focused.dataset.importAction:null,focusSummary=el.contains(focused)&&focused.tagName==='SUMMARY';
    el.innerHTML = `<div class="progress-heading"><strong>${paused ? '取り込みを一時停止中' : pending || active ? '原本を保存しています' : '取り込み結果'}：${completed} / ${jobs.length}ファイル</strong><span>${saved}件を新規保存 · 重複${duplicates}件 · 失敗${failed().length}ファイル${skipped?' · 対象外'+skipped+'ファイル':''}</span></div><progress max="${jobs.length}" value="${completed}" aria-label="ファイルの取り込み進捗"></progress><div class="import-actions">${pending ? `<button class="button small" data-import-action="${paused?'resume':'pause'}">${paused?'取り込みを再開':'取り込みを一時停止'}</button>` : ''}${retryCount?'<button class="button small" data-import-action="retry">失敗分を再試行</button>':''}${!pending&&!active?'<button class="text-button" data-import-action="dismiss">結果を閉じる</button>':''}<span class="field-hint">${paused&&active?'保存中の1ファイルが完了してから止まります。':pending?'このタブを開いたままお待ちください。保存は1ファイルずつ進みます。閉じてしまった場合も、同じフォルダーを同じ取り込み方で選び直すと、保存済みの原本はスキップします。':'同じ原本を同じ取り込み方で選び直しても、二重登録や上書きをしません。'}</span></div>${splitCount?`<div class="warning-list"><p>11ページ以上のPDFが${splitCount}件あります。ページごとに別の領収書が入っている場合は、下のボタンで取り込み直せます。1つの取引の続きの場合は、10ページ以下に分けてください。</p><button class="button small" data-import-action="split-failed">ページごとに別の領収書として取り込み直す</button></div>`:''}<details ${opened?'open':''}><summary>ファイルごとの結果・失敗理由</summary><div class="import-result-controls">${opened?resultsControls():''}</div><ul class="import-results">${opened?resultsRows():''}</ul></details>`;
    if(el.querySelector('.import-results'))el.querySelector('.import-results').scrollTop=scroll;
    if(focusAction)(el.querySelector(`[data-import-action="${focusAction}"]`)||el.querySelector(`[data-import-action="${paused?'resume':'pause'}"]`))?.focus({preventScroll:true});
    if(focusSummary)el.querySelector('summary')?.focus({preventScroll:true});
  }

  function open() {element('import-dialog').showModal();}
  return {setup, open, busy, addFiles, readEntry};
})();
