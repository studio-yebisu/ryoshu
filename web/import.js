'use strict';
// File handles stay in this browser tab. Originals accepted by the server persist locally.
window.RyoshuImport = (() => {
  const jobs = [];
  let controls, homeMounted=false;
  let config, handlers, running = false, paused = false, dragDepth = 0, nextId = 1, staged = [], resultsPage = 0, resultsFilter = 'all';
  const element = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const waiting = () => jobs.filter(j => j.status === 'waiting');
  const failed = () => jobs.filter(j => j.status === 'failed');
  const busy = () => running || waiting().length > 0;
  const isPaused = () => paused && waiting().length > 0;
  const labels = {waiting:'取り込み待ち',importing:'保存中',saved:'保存済み',duplicate:'重複のためスキップ',failed:'取り込み失敗',skipped:'対応していない形式'};

  function setup(settings, callbacks) {
    config = settings; handlers = callbacks;
    const dialog = element('import-dialog');
    dialog.innerHTML = `<form method="dialog" class="dialog-heading"><h2>領収書を読み込む</h2><button class="button small" aria-label="取り込み画面を閉じる">閉じる</button></form>`;
    controls=document.createElement('div');controls.className='import-controls';
    controls.innerHTML=`<div class="import-picker dropzone"><div class="import-illustration" aria-hidden="true"><svg viewBox="0 0 40 48"><path d="M10 3h16l8 8v33l-5-2-5 2-5-2-5 2-4-2Z"/><path d="M26 3v8h8M16 18h12M16 23h12M16 30l4 4 8-8"/></svg></div><h2>PDF・画像をここにドロップ</h2><div class="import-file-buttons"><button class="button" id="choose-files">ファイルを選ぶ</button><button class="button" id="choose-folder">フォルダーを選ぶ</button></div><p class="field-hint">PDF・JPG・PNG・WebP · 1ファイル20MBまで</p></div><div class="import-options-row"><p id="import-selection" role="status">ファイルを選んでから開始してください。</p><details class="pdf-options"><summary id="pdf-mode-summary">PDFは1ファイルを1つの取引として読み取ります（変更）</summary><fieldset><legend>PDFの取り込み方</legend><label class="checkbox-line"><input type="radio" name="pdf-mode" value="document" checked>1ファイルを1つの取引にする</label><p class="field-hint">続きのある請求書など。${config.limits.max_pages}ページまで。</p><label class="checkbox-line"><input type="radio" name="pdf-mode" value="pages">1ページずつ、別の領収書にする</label><p class="field-hint">スキャンしたレシートの束など。${config.limits.max_split_pages}ページまで。元PDFも保存します。</p><p class="field-hint">選んだPDFすべてに同じ方法を使います。取引の続きとレシートの束は、分けて選んでください。1ページの中に並んだ領収書は分割できません。</p></fieldset></details></div><div class="import-start-row"><button class="button primary" id="start-import" disabled>読み取りを開始</button><button class="text-button hidden" id="clear-import">選択を解除</button></div>`;
    dialog.append(controls);
    controls.addEventListener('change',event=>{if(event.target.name==='pdf-mode')updateSelection();});
    element('start-import').addEventListener('click',()=>{const mode=dialogMode();if(!mode||!staged.length)return;const files=staged;staged=[];updateSelection();queueFiles(files,mode);});
    element('clear-import').addEventListener('click',()=>{staged=[];updateSelection();});
    dialog.addEventListener('close',()=>{if(staged.length&&!homeMounted){staged=[];updateSelection();handlers.notice('選択したファイルの取り込みは開始していません。');}});
    element('choose-files').addEventListener('click', () => element('file-input').click());
    element('choose-folder').addEventListener('click', () => element('folder-input').click());
    for (const id of ['file-input', 'folder-input']) {
      element(id).addEventListener('change', event => {
        const mode = controls.querySelector('[name="pdf-mode"]:checked')?.value;
        addFiles([...event.target.files], mode); event.target.value = '';
      });
    }
    element('import-progress').addEventListener('toggle',event=>{if(event.target.tagName==='DETAILS'&&event.target.open&&element('import-progress').contains(event.target))syncResults();},true);
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
      if(action==='pause'||action==='resume'){const next=element('import-progress').querySelector(`[data-import-action="${paused?'resume':'pause'}"]`);if(next&&!next.classList.contains('hidden'))next.focus({preventScroll:true});}
    });
    document.addEventListener('dragenter', event => {
      if ([...event.dataTransfer.types].includes('Files')) {event.preventDefault();dragDepth++;element('upload-overlay').classList.remove('hidden');}
    });
    document.addEventListener('dragover', event => {if ([...event.dataTransfer.types].includes('Files')) event.preventDefault();});
    document.addEventListener('dragleave', () => {if (--dragDepth <= 0) {dragDepth = 0;element('upload-overlay').classList.add('hidden');}});
    document.addEventListener('drop', async event => {
      if (![...event.dataTransfer.types].includes('Files')) return;
      event.preventDefault();dragDepth = 0;element('upload-overlay').classList.add('hidden');
      const mode = dialog.open ? controls.querySelector('[name="pdf-mode"]:checked')?.value : undefined;
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
    staged=files;updateSelection();
    if(!homeMounted&&!element('import-dialog').open)element('import-dialog').showModal();
  }

  function dialogMode(){return controls.querySelector('[name="pdf-mode"]:checked')?.value;}
  function updateSelection(){
    const pdfs=staged.filter(f=>/\.pdf$/i.test(f.name)||f.type==='application/pdf').length;
    element('import-selection').textContent=staged.length?`${staged.length}ファイル選択中${pdfs?'（PDF '+pdfs+'ファイル）':''}。開始するまで読み取りは行いません。`:'ファイルを選んでから開始してください。';
    element('start-import').disabled=!staged.length||!dialogMode();
    element('clear-import').classList.toggle('hidden',!staged.length);
    element('pdf-mode-summary').textContent=dialogMode()==='pages'?'PDFは1ページずつ別の領収書として読み取ります（変更）':'PDFは1ファイルを1つの取引として読み取ります（変更）';
  }
  function setHome(visible){
    if(!controls)return;
    homeMounted=visible;
    const host=visible?element('home-import'):element('import-dialog');
    if(controls.parentElement!==host)host.append(controls);
  }

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
  function resultRow(j){return `<li data-job="${j.key}"><span>${escape(j.file.webkitRelativePath||j.file.name)}</span><strong>${labels[j.status]}${j.rows.length>1?'（'+j.rows.length+'ページ）':''}</strong>${j.message?`<p>${escape(j.message)}</p>`:''}${j.needsSplit?`<button class="text-button" data-import-split="${j.key}">1ページずつ別の領収書として取り込み直す</button>`:''}${j.rows.length?`<div>${j.rows.slice(0,3).map(r=>`<button class="text-button" data-import-open="${r.id}">${r.duplicate?'登録済みの明細を開く':'明細を開く'}</button>`).join(' ')}</div>`:''}</li>`;}
  function resultsControls(){const pages=Math.max(1,Math.ceil(resultJobs().length/100));resultsPage=Math.max(0,Math.min(resultsPage,pages-1));return `<div class="import-actions"><select id="import-result-filter" aria-label="取り込み結果を絞り込む"><option value="all" ${resultsFilter==='all'?'selected':''}>すべてのファイル</option><option value="problem" ${resultsFilter==='problem'?'selected':''}>失敗・対応していない形式</option></select><span>${resultsPage+1} / ${pages}ページ</span>${pages>1?`<button class="text-button" data-import-action="result-prev" ${resultsPage?'':'disabled'}>前へ</button><button class="text-button" data-import-action="result-next" ${resultsPage<pages-1?'':'disabled'}>次へ</button>`:''}</div>`;}
  function syncResults(){
    const el=element('import-progress'),controls=el.querySelector('.import-result-controls'),list=el.querySelector('.import-results');if(!list)return;
    const html=resultsControls(),key=[resultsPage,resultsFilter,Math.ceil(resultJobs().length/100)].join(':');
    if(controls.dataset.key!==key){const active=controls.contains(document.activeElement),action=document.activeElement?.dataset.importAction;controls.innerHTML=html;controls.dataset.key=key;if(active)(controls.querySelector(`[data-import-action="${action}"]:not(:disabled)`)||controls.querySelector('select'))?.focus({preventScroll:true});}
    const visible=resultJobs().slice(resultsPage*100,(resultsPage+1)*100),existing=new Map([...list.children].map(node=>[Number(node.dataset.job),node]));
    for(let index=0;index<visible.length;index++){
      const job=visible[index],signature=JSON.stringify([job.status,job.message,job.needsSplit,job.rows]);let node=existing.get(job.key);
      if(!node||node.dataset.signature!==signature){const template=document.createElement('template');template.innerHTML=resultRow(job);const next=template.content.firstElementChild;next.dataset.signature=signature;if(node)node.replaceWith(next);node=next;}
      if(list.children[index]!==node)list.insertBefore(node,list.children[index]||null);
      existing.delete(job.key);
    }
    for(const node of existing.values())node.remove();
  }
  function renderProgress(){
    const el=element('import-progress');
    if(!jobs.length){el.classList.add('hidden');el.replaceChildren();resultsPage=0;resultsFilter='all';handlers.progress?.();return;}
    el.classList.remove('hidden');
    if(!el.querySelector('.progress-heading'))el.innerHTML=`<div class="progress-heading"><strong></strong><span></span></div><progress aria-label="ファイルの取り込み進捗"></progress><div class="import-actions"><button class="button small" data-import-action="pause">取り込みを一時停止</button><button class="button small" data-import-action="resume">取り込みを再開</button><button class="button small" data-import-action="retry">失敗分を再試行</button><button class="text-button" data-import-action="dismiss">結果を閉じる</button><span class="field-hint" data-import-hint></span></div><div class="warning-list hidden" data-split-warning><p></p><button class="button small" data-import-action="split-failed">ページごとに別の領収書として取り込み直す</button></div><details><summary>ファイルごとの結果・失敗理由</summary><div class="import-result-controls"></div><ul class="import-results"></ul></details>`;
    const pending=waiting().length,active=jobs.filter(j=>j.status==='importing').length,completed=jobs.length-pending-active;
    el.classList.toggle('is-saving',!!(pending||active));
    const saved=jobs.reduce((n,j)=>n+j.rows.filter(r=>!r.duplicate).length,0),duplicates=jobs.reduce((n,j)=>n+j.duplicate,0),skipped=jobs.filter(j=>j.status==='skipped').length;
    el.classList.toggle('has-issues',!!(failed().length||skipped));
    el.classList.toggle('has-duplicates',duplicates>0);
    el.classList.toggle('has-new-records',saved>0);
    const splitCount=failed().filter(j=>j.needsSplit).length,retryCount=failed().length-splitCount;
    el.querySelector('.progress-heading strong').textContent=`${paused?'取り込みを一時停止中':pending||active?'原本を保存しています':'原本の保存結果'}：${completed} / ${jobs.length}ファイル`;
    el.querySelector('.progress-heading span').textContent=`${saved}件を新規保存 · 重複${duplicates}件 · 失敗${failed().length}ファイル${skipped?' · 未対応形式'+skipped+'ファイル':''}`;
    const progress=el.querySelector('progress');progress.max=jobs.length;progress.value=completed;
    for(const [action,show] of Object.entries({pause:pending&&!paused,resume:pending&&paused,retry:retryCount>0,dismiss:!pending&&!active}))el.querySelector(`[data-import-action="${action}"]`).classList.toggle('hidden',!show);
    el.querySelector('[data-import-hint]').textContent=paused&&active?'保存中の1ファイルが完了してから止まります。':pending?'このタブを開いたままお待ちください。保存は1ファイルずつ進みます。タブを閉じた場合も、同じファイルを同じ取り込み方で選び直すと、保存済みの原本はスキップします。':'同じ原本を同じ取り込み方で選び直しても、二重登録や上書きをしません。';
    const warning=el.querySelector('[data-split-warning]');warning.classList.toggle('hidden',!splitCount);
    if(splitCount)warning.querySelector('p').textContent=`11ページ以上のPDFが${splitCount}件あります。ページごとに別の領収書が入っている場合は、下のボタンで取り込み直せます。1つの取引の続きの場合は、10ページ以下に分けてください。`;
    if(el.querySelector('details').open)syncResults();
    handlers.progress?.();
  }

  function open() {setHome(false);element('import-dialog').showModal();}
  return {setup, open, busy, paused:isPaused, addFiles, readEntry, setHome};
})();
