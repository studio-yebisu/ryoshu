'use strict';
window.RyoshuSecurity = (() => {
  let token = sessionStorage.getItem('ryoshu-session') || '';
  const nativeFetch = window.fetch.bind(window);
  const escape = value => String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  async function request(path, options={}) {
    const url = new URL(path, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/api/')) throw new Error('このアプリの接続先だけを開けます。');
    const headers = new Headers(options.headers);
    headers.set('Authorization','Bearer '+token);
    headers.set('X-Ledger-Request','1');
    return nativeFetch(url,{...options,headers,credentials:'omit',redirect:'error',cache:'no-store'});
  }
  async function json(path,body) {
    const response = await request(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(typeof result.detail==='string'?result.detail:'入力した内容を確認してください。');
    return result;
  }

  async function prepare() {
    const fragment = new URLSearchParams(location.hash.slice(1));
    const bootstrap = fragment.get('open');
    if (bootstrap) {
      history.replaceState(null,'',location.pathname+location.search);
      token = '';
      sessionStorage.removeItem('ryoshu-session');
      const response = await nativeFetch('/api/session',{method:'POST',credentials:'omit',redirect:'error',cache:'no-store',headers:{'Authorization':'Bootstrap '+bootstrap,'X-Ledger-Request':'1'}});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Ryoshu.cmdから開き直してください。');
      token = result.token;
      sessionStorage.setItem('ryoshu-session',token);
    }
    const status = await json('/api/security');
    if (status.ready) return;
    if(status.can_retry){
      document.querySelector('#upload-top').disabled=true;
      document.querySelectorAll('button.nav-item').forEach(el=>el.disabled=true);
      document.querySelector('#main-content').innerHTML=`<section class="panel settings-card protection-setup"><h2>帳簿を確認できませんでした</h2><p id="retry-protection-error" role="alert">${escape(status.error||'中断した保護の処理を再開できます。')}</p><p>保存先と原本を確認してから、もう一度お試しください。復元する場合は「バックアップから復元.cmd」を使います。空の帳簿へ自動で切り替えることはありません。</p><button class="button primary" id="retry-protection">帳簿をもう一度確認</button><p><button class="text-button" id="recover-protection">復元用パスワードで帳簿を開き直す</button></p><p><a href="/help.html#security" target="_blank" rel="noreferrer">保存と復元の説明</a></p></section>`;
      const recovered=await new Promise(resolve=>{
        document.querySelector('#recover-protection').onclick=()=>resolve(false);
        document.querySelector('#retry-protection').onclick=async event=>{
          const button=event.currentTarget;button.disabled=true;
          try{await json('/api/security/retry',{});document.querySelector('#upload-top').disabled=false;document.querySelectorAll('button.nav-item').forEach(el=>el.disabled=false);resolve(true);}
          catch(error){document.querySelector('#retry-protection-error').textContent=error.message;button.disabled=false;}
        };
      });
      if(recovered)return;
    }
    const fresh = status.mode==='new';
    document.querySelector('#upload-top').disabled=true;
    document.querySelectorAll('button.nav-item').forEach(el=>el.disabled=true);
    document.querySelector('#page-title').textContent=fresh?'はじめに、帳簿を保護します':'復元用パスワードで開く';
    document.querySelector('#page-description').textContent='領収書の読み取りからCSV出力まで、このPC内で処理します。';
    document.querySelector('#usage-guidance').classList.add('hidden');
    document.querySelector('#main-content').innerHTML=`<section class="panel settings-card protection-setup"><h2>${fresh?'復元用パスワードを決める':'復元用パスワードで帳簿を開く'}</h2><p>${fresh?'通常はWindowsが自動で帳簿を開くため、毎回の入力は不要です。PCの移行やバックアップの復元に使うパスワードを設定してください。':'別のWindowsアカウントやPCでは、自動解除できません。設定した復元用パスワードを入力してください。'}</p>${status.migrate_legacy?'<p>既存の帳簿と原本を暗号化します。元の内容を照合してから移行します。旧版のRyoshuは終了してください。</p>':''}<form id="protection-form"><label class="field"><span class="field-label">復元用パスワード（12〜256文字）</span><input name="password" type="password" minlength="12" maxlength="256" autocomplete="${fresh?'new-password':'current-password'}" required></label>${fresh?'<label class="field"><span class="field-label">もう一度入力</span><input name="confirmation" type="password" minlength="12" maxlength="256" autocomplete="new-password" required></label><label class="checkbox-line"><input type="checkbox" required>復元用パスワードを安全な場所に保管します</label><p class="field-hint">パスワードを忘れ、Windowsの鍵も使えなくなると復元できません。パスワード管理アプリなどに保管してください。</p>':''}<p id="protection-error" role="alert"></p><button class="button primary" type="submit">${fresh?'保護してはじめる':'帳簿を開く'}</button></form><details><summary>保護するデータと範囲</summary><p>帳簿DB・保存済みの原本・プレビュー・バックアップを暗号化します。読み取り中は利用者専用の作業フォルダーに一時ファイルを作り、終了後に片付けます。同じWindowsアカウントで動く不正なソフトや、開いた画面を他人に操作されることまでは防げません。</p><p>自分で書き出すCSV・原本、旧版のZIPや移行前に残ったディスク上の領域は別途管理してください。</p><a href="/help.html#security" target="_blank" rel="noreferrer">詳しい説明</a></details></section>`;
    await new Promise(resolve=>{
      document.querySelector('#protection-form').addEventListener('submit',async event=>{
        event.preventDefault();
        const form=event.currentTarget,button=form.querySelector('button[type=submit]'),message=document.querySelector('#protection-error');
        const password=form.elements.password.value,confirmation=form.elements.confirmation?.value;
        if(fresh&&password!==confirmation){message.textContent='確認用のパスワードが一致しません。';return;}
        button.disabled=true;button.textContent='帳簿を確認しています…';message.textContent='';
        try{
          await json('/api/security/open',{password,...(fresh?{confirmation}:{})});
          form.reset();
          document.querySelector('#upload-top').disabled=false;
          document.querySelectorAll('button.nav-item').forEach(el=>el.disabled=false);
          document.querySelector('#usage-guidance').classList.remove('hidden');
          resolve();
        }catch(error){message.textContent=error.message;button.disabled=false;button.textContent=fresh?'保護してはじめる':'帳簿を開く';}
      });
    });
  }

  async function saveResponse(response, fallback, handle=null) {
    if(!response.ok){const result=await response.json();throw new Error(result.detail||'保存できませんでした。');}
    if(handle&&response.body){
      const output=await handle.createWritable();
      await response.body.pipeTo(output);
      return;
    }
    if(Number(response.headers.get('Content-Length'))>256*1024*1024){
      if(response.body)await response.body.cancel();
      throw new Error('大きなバックアップの保存には、ChromeまたはEdgeでRyoshu.cmdの起動リンクを開いてください。このブラウザーでの保存は256MBまでです。');
    }
    const blob=await response.blob(),url=URL.createObjectURL(blob),link=document.createElement('a');
    const header=response.headers.get('Content-Disposition')||'';
    const encoded=header.match(/filename\*=utf-8''([^;]+)/i),quoted=header.match(/filename="([^"]+)"/i);
    let name=fallback;
    try{name=encoded?decodeURIComponent(encoded[1]):quoted?quoted[1]:fallback;}catch{}
    link.href=url;link.download=name.replace(/[\\/\x00-\x1f]/g,'_');link.hidden=true;
    document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  }

  function backup() {
    const dialog=document.createElement('dialog');dialog.className='backup-dialog';dialog.setAttribute('aria-label','暗号化バックアップを保存');
    dialog.innerHTML='<form><h2>バックアップを保存</h2><p>帳簿・原本・修正履歴をパスワードで暗号化します。別のPCへの復元にも使えます。</p><label class="field"><span class="field-label">復元用パスワード</span><input name="password" type="password" minlength="12" maxlength="256" autocomplete="current-password" required></label><details><summary>原本が壊れた場合の救出用</summary><label class="checkbox-line"><input name="allow_missing" type="checkbox">不足・破損した原本を省いて、残ったデータを保存する</label><p class="field-hint">通常はオフで使います。省いた原本は復元できません。不足件数は復元後にも表示します。</p></details><p role="alert"></p><div class="sample-actions"><button class="button primary" type="submit">暗号化して保存</button><button class="button" type="button" data-cancel>戻る</button></div><p class="field-hint">件数によって数分かかります。保存したファイルとパスワードを別々の安全な場所に保管してください。</p></form>';
    document.body.append(dialog);dialog.showModal();
    dialog.addEventListener('close',()=>{dialog.querySelector('form').reset();dialog.remove();},{once:true});
    dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();
    dialog.querySelector('form').onsubmit=async event=>{
      event.preventDefault();const form=event.currentTarget,button=form.querySelector('[type=submit]');
      button.disabled=true;button.textContent='バックアップを作成しています…';form.querySelector('[role=alert]').textContent='';
      try{
        const handle=window.showSaveFilePicker?await window.showSaveFilePicker({suggestedName:'ryoshu-backup.ryoshubak',types:[{description:'Ryoshu暗号化バックアップ',accept:{'application/octet-stream':['.ryoshubak']}}]}):null;
        await saveResponse(await request('/api/backup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:form.elements.password.value,allow_missing:form.elements.allow_missing.checked})}),'ryoshu-backup.ryoshubak',handle);dialog.close();
      }
      catch(error){if(error.name!=='AbortError')form.querySelector('[role=alert]').textContent=error.message;button.disabled=false;button.textContent='暗号化して保存';}
    };
  }
  function changePassword(){
    const dialog=document.createElement('dialog');dialog.className='backup-dialog';dialog.setAttribute('aria-label','復元用パスワードを再設定');
    dialog.innerHTML='<form><h2>復元用パスワードを再設定</h2><p>このWindowsアカウントで開ける帳簿の、復元用パスワードを変更します。</p><label class="field"><span class="field-label">新しいパスワード（12〜256文字）</span><input name="password" type="password" minlength="12" maxlength="256" autocomplete="new-password" required></label><label class="field"><span class="field-label">もう一度入力</span><input name="confirmation" type="password" minlength="12" maxlength="256" autocomplete="new-password" required></label><label class="checkbox-line"><input type="checkbox" required>変更後に新しいバックアップを保存します</label><p class="field-hint">以前のバックアップには、以前のパスワードが必要です。パスワードを忘れた場合も、Windowsで帳簿を開けるうちに再設定できます。</p><p role="alert"></p><div class="sample-actions"><button class="button primary" type="submit">再設定する</button><button class="button" type="button" data-cancel>戻る</button></div></form>';
    document.body.append(dialog);dialog.showModal();dialog.addEventListener('close',()=>{dialog.querySelector('form').reset();dialog.remove();},{once:true});
    dialog.querySelector('[data-cancel]').onclick=()=>dialog.close();
    dialog.querySelector('form').onsubmit=async event=>{
      event.preventDefault();const form=event.currentTarget,button=form.querySelector('[type=submit]'),message=form.querySelector('[role=alert]');
      const password=form.elements.password.value,confirmation=form.elements.confirmation.value;
      if(password!==confirmation){message.textContent='確認用のパスワードが一致しません。';return;}
      button.disabled=true;
      try{const result=await json('/api/security/password',{password,confirmation});dialog.close();alert(result.message);}
      catch(error){message.textContent=error.message;button.disabled=false;}
    };
  }
  document.addEventListener('click',async event=>{
    if(event.target.closest('[data-action="change-password"]')){event.preventDefault();changePassword();return;}
    const button=event.target.closest('[data-action="backup"]');
    if(button){event.preventDefault();backup();return;}
    const link=event.target.closest('a[href^="/api/"]');
    if(!link||event.button!==0)return;
    event.preventDefault();
    try{await saveResponse(await request(link.getAttribute('href')),'ryoshu-download');}
    catch(error){alert(error.message);}
  });

  const images=new Map();let scheduled=false;
  function loadImage(element,item){
    if(item.started)return;
    item.started=true;
    request(element.dataset.privateSrc,{signal:item.abort.signal}).then(response=>{if(!response.ok)throw new Error('preview');return response.blob();}).then(blob=>{
      if(!element.isConnected)return;
      item.url=URL.createObjectURL(blob);element.src=item.url;
    }).catch(error=>{if(error.name!=='AbortError')element.alt=element.alt||'プレビューを表示できません';});
  }
  const imageObserver=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{
    for(const entry of entries)if(entry.isIntersecting){imageObserver.unobserve(entry.target);const item=images.get(entry.target);if(item)loadImage(entry.target,item);}
  },{rootMargin:'300px'}):null;
  function hydrate(){
    scheduled=false;
    for(const [element,item] of images)if(!element.isConnected){imageObserver?.unobserve(element);item.abort.abort();if(item.url)URL.revokeObjectURL(item.url);images.delete(element);}
    for(const element of document.querySelectorAll('img[data-private-src]')){
      if(images.has(element))continue;
      const item={abort:new AbortController(),url:null};images.set(element,item);
      if(imageObserver&&element.loading==='lazy')imageObserver.observe(element);else loadImage(element,item);
    }
  }
  new MutationObserver(()=>{if(!scheduled){scheduled=true;queueMicrotask(hydrate);}}).observe(document.body,{childList:true,subtree:true});
  return {prepare,fetch:request,backup};
})();
