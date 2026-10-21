'use strict';
(() => {
  const report = window.RYOSHU_RESULTS;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const money = value => value == null ? '未確定' : `${Number(value).toLocaleString('ja-JP')}円`;
  const decisions = {propose:'提案',hold:'保留',asset_review:'資産検討',block:'停止'};
  const modes = {digital:'電子PDF',flatbed:'平面スキャン',thermal_fade:'薄い印字',fax:'FAX',camera:'斜め撮影',rotation:'回転'};
  const metrics = [['ocr_completed','OCR完了'],['total_exact','金額・円額未確定'],['date_exact','日付'],['account_exact','暫定科目']];
  document.querySelector('#metrics').innerHTML = metrics.map(([key,label]) => {
    const after = report.after[key], before = report.before[key];
    return `<div class="metric"><small>${label}</small><strong>${after.matched}<small style="display:inline;font-size:17px"> / ${after.evaluated}</small></strong><span class="before">修正前 ${before.matched} / ${before.evaluated}</span></div>`;
  }).join('');
  const rows = report.cases.map(c => {
    const actual = c.actual, expected = c.expected;
    const different = c.difference_fields.length > 0;
    const manual = c.manual_corrections;
    const expectedFields = [expected.date,expected.merchant,money(expected.total),expected.acceptable_accounts.join(' / ') || '空欄',decisions[expected.expected_decision]];
    const actualFields = [actual.date,actual.merchant,money(actual.total),actual.account || '空欄',decisions[actual.decision]];
    const table = ['日付','支払先','金額','科目','扱い'].map((label,i) => `<tr><th>${label}</th><td>${esc(expectedFields[i])}</td><td>${esc(actualFields[i])}</td></tr>`).join('');
    return {c, different, search:JSON.stringify(c).toLowerCase(), html:`<article class="card"><a class="preview" href="../../samples/receipts-100-v2/${esc(c.file)}" target="_blank" rel="noopener"><img loading="lazy" src="../../samples/receipts-100-v2/thumbnails/${c.id}.jpg" alt="${esc(c.id+' '+c.title)}"></a><div class="card-body"><span class="badge${different?' diff':''}">${esc(c.review_label)}</span><h3>${esc(c.id)} · ${esc(c.title)}</h3><p class="sub">${esc(modes[c.acquisition]||c.acquisition)} · ${esc(c.model_author)}</p><dl class="values"><dt>支払先</dt><dd>${esc(actual.merchant||'未確定')}</dd><dt>金額</dt><dd>${esc(money(actual.total))}</dd><dt>科目</dt><dd>${esc(actual.account||'未確定')} · ${esc(decisions[actual.decision])}</dd></dl><p class="note">${esc(c.review_note)}</p>${manual.map(m=>`<p class="manual"><strong>確認欄を手動補正</strong><br>${esc(m.before)} → ${esc(m.after)}<br>${esc(m.basis)}。元のAI候補と点数は保持。</p>`).join('')}<details><summary>期待値と自動読取を比べる</summary><table><thead><tr><th></th><th>期待値</th><th>自動読取</th></tr></thead><tbody>${table}</tbody></table><p>${esc(actual.reason)}</p><p>${esc([...(actual.warnings||[]),...(actual.blocking_reasons||[]),...(actual.review_questions||[])].join(' / '))}</p><p class="sub">実行元: ${esc(c.source_run)}<br>PDF SHA-256: ${esc(c.sha256)}</p></details></div></article>`};
  });
  function render() {
    const query = document.querySelector('#search').value.toLowerCase().trim();
    const filter = document.querySelector('#filter').value;
    const selected = rows.filter(r => (!query || r.search.includes(query)) && (filter === 'all' || filter === 'different' && r.different || filter === 'manual' && r.c.manual_corrections.length || filter === 'block' && r.c.actual.decision === 'block'));
    document.querySelector('#cases').innerHTML = selected.map(r=>r.html).join('') || '<p>該当するケースがありません。</p>';
    document.querySelector('#count').textContent = `${selected.length} / 100件`;
  }
  document.querySelector('#search').addEventListener('input',render);
  document.querySelector('#filter').addEventListener('change',render);
  render();
})();
