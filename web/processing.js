'use strict';
// The progress view observes saved records. It never changes a receipt or a job.
window.RyoshuProcessing = (() => {
  const busy = row => row.status === 'queued' || row.status === 'processing';
  function processingState(row) {
    if (!row) return 'syncing';
    if (row.status === 'processing') return 'processing';
    if (row.status === 'queued') return 'queued';
    if (row.status === 'error') return 'failed';
    if (row.status === 'review') return row.error ? 'partial' : 'complete';
    if (row.status === 'confirmed') return 'confirmed';
    return 'excluded';
  }
  const terminal = kind => ['complete', 'confirmed', 'partial', 'failed'].includes(kind);
  const successful = kind => kind === 'complete' || kind === 'confirmed';
  const timestamp = value => Number.isFinite(Date.parse(value)) ? Date.parse(value) : 0;
  function summarize(records, ids, names = new Map()) {
    const byId = new Map(records.map(row => [row.id, row]));
    const rows = [...ids].map((id, index) => {
      const row = byId.get(id);
      return {id, index, row, kind:processingState(row), name:row?.original_name || names.get(id) || '保存した領収書'};
    }).filter(item => item.kind !== 'excluded');
    const counts = {total:rows.length, completed:0, active:0, waiting:0, attention:0, syncing:0, processed:0};
    for (const item of rows) {
      if (successful(item.kind)) counts.completed++;
      else if (item.kind === 'processing') counts.active++;
      else if (item.kind === 'queued') counts.waiting++;
      else if (item.kind === 'syncing') counts.syncing++;
      else counts.attention++;
      if (terminal(item.kind)) counts.processed++;
    }
    return {...counts, rows, otherAttention:records.filter(row => !ids.has(row.id) && ['failed','partial'].includes(processingState(row))).length,
      busy:counts.active + counts.waiting + counts.syncing,
      current:rows.find(item => item.kind === 'processing'),
      next:rows.filter(item => ['queued', 'syncing'].includes(item.kind)).slice(0, 4),
      recent:rows.filter(item => terminal(item.kind)).sort((a,b) => timestamp(b.row.updated_at)-timestamp(a.row.updated_at) || b.index-a.index).slice(0, 6)};
  }
  function createTracker() {
    let ids = new Set(), names = new Map(), seen = new Set(), previous = new Map(), scope = 'remaining';
    let initialized = false, stale = false, fetchedAt = 0, last = null, dismissed = false;
    function reset() {ids = new Set(); names = new Map(); seen = new Set(); previous = new Map(); last = null; dismissed = false;}
    function begin() {
      if (!last || (!last.busy && !last.importing)) reset();
      initialized = true; scope = 'current'; dismissed = false;
    }
    function imported(result) {
      for (const row of result?.imported || []) if (row.id && row.duplicate === false) {
        ids.add(row.id); if (row.name) names.set(row.id, row.name); dismissed = false;
      }
    }
    function observe(records, options = {}) {
      const work = records.filter(busy), unseen = work.filter(row => !ids.has(row.id));
      if (!initialized) {
        initialized = true;
        // A reload cannot reconstruct a previous batch: capture only work left now.
        if (!work.length) {
          const attention = records.filter(row => ['failed','partial'].includes(processingState(row)));
          if (attention.length) {scope = 'attention'; for (const row of attention) ids.add(row.id);}
        }
      } else if (unseen.length && last && !last.busy && !last.importing && !options.importing) {
        reset(); scope = 'remaining';
      }
      if (work.length && (!last || !last.busy)) dismissed = false;
      if (work.length && scope === 'attention') scope = 'remaining';
      for (const row of work) ids.add(row.id);
      const nextFetch = Number(options.fetchedAt) || 0;
      if (nextFetch > fetchedAt) {fetchedAt = nextFetch; stale = false;}
      const snapshot = summarize(records, ids, names), newlyCompleted = new Set();
      for (const item of snapshot.rows) {
        const before = previous.get(item.id);
        // Only pipeline completion clears a previous read error; a manual confirm does not.
        const arrived = !previous.has(item.id) || ['queued','processing','syncing'].includes(before)
          || (item.kind === 'complete' && ['failed','partial'].includes(before));
        if (successful(item.kind) && arrived) {
          const key = item.id + ':' + (item.row.version ?? item.row.updated_at ?? '');
          if (!seen.has(key)) {seen.add(key); newlyCompleted.add(item.id);}
        }
        previous.set(item.id, item.kind);
      }
      last = {...snapshot, importing:!!options.importing, scope, stale, fetchedAt, dismissed, newlyCompleted};
      return last;
    }
    function connectionFailed() {stale = true; if (last) last.stale = true;}
    function dismiss() {dismissed = true; if (last) last.dismissed = true;}
    return {begin, imported, observe, connectionFailed, dismiss, get last(){return last;}};
  }
  function create(target) {
    const tracker = createTracker(), doc = target.ownerDocument;
    let mounted = false, snapshot = null;
    const text = (selector, value) => {const el = target.querySelector(selector); if (el && el.textContent !== String(value)) el.textContent = value;};
    function mount() {
      if (mounted) return;
      mounted = true;
      target.setAttribute('aria-live', 'off');
      target.setAttribute('aria-label', '領収書の読み取り状況');
      target.innerHTML = `<div class="pp-header"><div><p class="pp-kicker" data-processing-scope></p><h2 data-processing-title></h2></div><div class="pp-count"><strong><span data-processing-count="processed">0</span><small> / <span data-processing-count="total">0</span>件</small></strong><span>処理済み</span></div></div>
        <progress class="pp-progress" aria-label="保存できた領収書の処理進捗"></progress>
        <div class="pp-stats"><div><strong data-processing-completed data-processing-count="completed">0</strong><span>読取完了</span></div><div><strong data-processing-active data-processing-count="active">0</strong><span>処理中</span></div><div><strong data-processing-waiting data-processing-count="waiting">0</strong><span>待機中</span></div><div><strong data-processing-attention data-processing-count="attention">0</strong><span>再確認・再試行</span></div></div>
        <p class="pp-connection hidden" data-processing-connection role="status">進捗を更新できません。最後に確認できた状態を表示しています。接続が戻ると更新します。</p>
        <div class="pp-current" data-processing-current><div class="pp-scan" aria-hidden="true"><div class="pp-scan-sheet"><i></i><i></i><i></i><span class="pp-scan-line"></span></div><span class="pp-scan-dot"></span></div><div class="pp-current-copy"><span class="pp-current-label" data-processing-current-label>いま、この領収書を処理しています</span><strong data-processing-file></strong><span class="pp-stage" data-processing-stage></span><div class="pp-stage-track" aria-hidden="true"><span data-processing-phase="ocr">文字の読み取り</span><span data-processing-phase="model">科目候補の作成</span></div><span class="pp-elapsed" data-processing-elapsed></span></div></div>
        <div class="pp-columns"><section><h3>最近の処理結果</h3><ul class="pp-list" data-processing-recent></ul><p class="pp-empty" data-processing-recent-empty>読み取りが終わると、ここに順番に並びます。</p></section><section><h3>このあと読み取る領収書</h3><ul class="pp-list" data-processing-next></ul><p class="pp-empty" data-processing-next-empty></p><p class="pp-more" data-processing-more></p></section></div>
        <div class="pp-footer"><p data-processing-note></p><div class="pp-actions"><button class="button small" type="button" data-action="retry-errors">失敗した読み取りを再試行</button><a class="button primary" href="#results" data-view="results">読み取った内容を見る</a><button class="text-button" type="button" data-processing-dismiss>表示を閉じる</button></div></div><span class="visually-hidden" data-processing-announcement role="status" aria-live="polite" aria-atomic="true"></span>`;
    }
    function rowLabel(item) {
      return {complete:'読取完了', confirmed:'確認済み', partial:'一部読取・要確認', failed:'読み取り失敗', queued:'待機中', processing:'処理中', syncing:'状態を更新中'}[item.kind];
    }
    function updateList(selector, items, fresh) {
      const list = target.querySelector(selector), old = new Map([...list.children].map(el => [el.dataset.processingId, el]));
      for (const [index, item] of items.entries()) {
        let node = old.get(item.id); old.delete(item.id);
        if (!node) {
          node = doc.createElement('li'); node.className = 'pp-item'; node.dataset.processingId = item.id;
          const icon = doc.createElement('span'); icon.className = 'pp-item-icon'; icon.setAttribute('aria-hidden', 'true');
          const copy = doc.createElement('div'); copy.className = 'pp-item-copy';
          const name = doc.createElement('span'); name.className = 'pp-item-name';
          const status = doc.createElement('span'); status.className = 'pp-status';
          copy.append(name,status); node.append(icon,copy);
        }
        node.dataset.state = item.kind;
        node.querySelector('.pp-item-name').textContent = item.name;
        node.querySelector('.pp-status').textContent = rowLabel(item);
        node.querySelector('.pp-item-icon').textContent = successful(item.kind) ? '✓' : ['partial','failed'].includes(item.kind) ? '!' : '·';
        if (fresh.has(item.id) && successful(item.kind)) {
          node.classList.add('is-new');
          node.addEventListener('animationend', () => node.classList.remove('is-new'), {once:true});
        } else if (!successful(item.kind)) node.classList.remove('is-new');
        if (list.children[index] !== node) list.insertBefore(node, list.children[index] || null);
      }
      for (const node of old.values()) node.remove();
    }
    function paintClock() {
      if (!snapshot || target.classList.contains('hidden')) return;
      const stale = snapshot.stale || !!(snapshot.busy && snapshot.fetchedAt && Date.now()-snapshot.fetchedAt > 9000);
      target.classList.toggle('is-stale', stale);
      target.dataset.state = stale ? 'stale' : snapshot.busy || snapshot.importing ? 'processing' : snapshot.attention ? 'attention' : 'finished';
      target.querySelector('[data-processing-connection]').classList.toggle('hidden', !stale);
      text('[data-processing-current-label]', stale ? '最後に確認できた処理' : 'いま、この領収書を処理しています');
      const start = timestamp(snapshot.current?.row.updated_at), end = stale ? snapshot.fetchedAt : Date.now();
      const seconds = Math.max(0, Math.floor((end-start)/1000));
      text('[data-processing-elapsed]', start ? `この工程を始めて ${seconds < 60 ? seconds+'秒' : Math.floor(seconds/60)+'分'+seconds%60+'秒'}` : '処理はこのPC内で進みます');
    }
    function render(records, options = {}) {
      snapshot = tracker.observe(records, options);
      const visible = (snapshot.total || snapshot.importing) && !snapshot.dismissed;
      target.classList.toggle('hidden', !visible);
      if (!visible) return snapshot;
      mount();
      const running = !!(snapshot.busy || snapshot.importing);
      target.classList.toggle('is-busy', running); target.classList.toggle('is-finished', !running);
      target.classList.toggle('has-attention', !!snapshot.attention);
      text('[data-processing-scope]', snapshot.scope === 'current' ? '今回の読み取り' : snapshot.scope === 'attention' ? '確認が必要な読み取り' : 'この画面を開いた時点の残りの処理');
      text('[data-processing-title]', snapshot.active ? '1件ずつ、読み取っています' : snapshot.waiting ? '順番に読み取ります' : snapshot.syncing || snapshot.importing ? '原本を保存しながら読み取っています' : snapshot.attention ? '処理が終わりました。再確認が必要な領収書があります' : '読み取りが完了しました');
      for (const key of ['processed','total','completed','active','waiting','attention']) text(`[data-processing-count="${key}"]`, snapshot[key]);
      const progress = target.querySelector('.pp-progress'); progress.max = snapshot.total || 1;
      if (snapshot.total) progress.value = snapshot.processed; else progress.removeAttribute('value');
      progress.setAttribute('aria-valuetext', `処理済み${snapshot.processed}件、対象${snapshot.total}件。読取完了${snapshot.completed}件、再確認・再試行${snapshot.attention}件。`);
      const current = target.querySelector('[data-processing-current]'); current.classList.toggle('hidden', !snapshot.current);
      current.dataset.currentId = snapshot.current?.id || '';
      text('[data-processing-file]', snapshot.current?.name || '');
      const model = snapshot.current?.row.stage === 'model';
      text('[data-processing-stage]', model ? '科目の候補を作成中' : '文字を読み取り中');
      current.dataset.phase = model ? 'model' : 'ocr';
      for (const phase of ['ocr','model']) {
        const node = target.querySelector(`[data-processing-phase="${phase}"]`);
        node.classList.toggle('is-active', model ? phase === 'model' : phase === 'ocr');
        node.classList.toggle('is-done', model && phase === 'ocr');
      }
      updateList('[data-processing-recent]', snapshot.recent, snapshot.newlyCompleted);
      updateList('[data-processing-next]', snapshot.next, new Set());
      target.querySelector('[data-processing-recent-empty]').classList.toggle('hidden', !!snapshot.recent.length);
      const nextEmpty = target.querySelector('[data-processing-next-empty]'); nextEmpty.classList.toggle('hidden', !!snapshot.next.length);
      nextEmpty.textContent = snapshot.importing ? '保存できた原本から順に追加します。' : snapshot.active ? '待機中の領収書はありません。この1件で終わります。' : '待機中の領収書はありません。';
      text('[data-processing-more]', snapshot.waiting + snapshot.syncing > snapshot.next.length ? `ほか ${snapshot.waiting + snapshot.syncing - snapshot.next.length}件が順番を待っています` : '');
      const note = snapshot.importing ? '原本を保存中です。保存できた領収書から対象件数へ加わります。' : snapshot.syncing ? '保存済みの領収書の状態を更新しています。' : running ? '保存後の読み取りは画面を閉じても続きます。PCのスリープ中は進みません。' : '読取完了は、内容の正しさや経費の確定を表すものではありません。原本と候補を確認してください。';
      text('[data-processing-note]', note + (snapshot.otherAttention ? ` 以前の読み取りにも、再確認・再試行が必要な領収書が${snapshot.otherAttention}件あります。` : ''));
      target.querySelector('[data-action="retry-errors"]').classList.toggle('hidden', !snapshot.attention && !snapshot.otherAttention);
      target.querySelector('[data-processing-dismiss]').classList.toggle('hidden', running);
      const announcement = `読取完了${snapshot.completed}件、待機中${snapshot.waiting}件、再確認・再試行${snapshot.attention}件。`;
      text('[data-processing-announcement]', announcement);
      paintClock(); return snapshot;
    }
    const dismissHandler = event => {if (event.target.closest('[data-processing-dismiss]') && !snapshot?.busy && !snapshot?.importing) {tracker.dismiss(); target.classList.add('hidden');}};
    target.addEventListener('click', dismissHandler);
    const timer = setInterval(() => {if (snapshot?.busy || snapshot?.importing) paintClock();}, 1000);
    return {begin:tracker.begin, imported:tracker.imported, render,
      connectionFailed(){tracker.connectionFailed(); if (snapshot && mounted) {snapshot.stale = true; paintClock();}},
      destroy(){clearInterval(timer); target.removeEventListener('click', dismissHandler);}};
  }
  return {create, createTracker, processingState, summarize};
})();
