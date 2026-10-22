'use strict';
window.RyoshuChat = (() => {
  let bridge, dialog, connection, targets=[], models=[], model='', messages=[], session=null, proposal=null;
  let busy=false, loginTimer, input='', allowed=false, loginUrl='', applyUnknown=false;
  const labels={date:'取引日',merchant:'支払先',total:'合計金額',description:'購入内容・摘要',account:'勘定科目',payment:'支払元',business_percent:'事業割合',purpose:'業務での用途',tax_rate:'記載税率',tax_amount:'記載消費税',invoice_number:'登録番号',export_tax:'出力用の税区分'};
  const q=selector=>dialog.querySelector(selector);
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const shown=(field,value)=>{
    if(value===null||value==='')return '未入力';
    if(field==='payment')return bridge.payments()[value]?.label||value;
    if(field==='business_percent')return value+'%';
    return String(value);
  };
  function preserve(){if(q('#chat-message'))input=q('#chat-message').value;if(q('#chat-allow'))allowed=q('#chat-allow').checked;}
  function render(){
    if(!dialog.open)return;
    preserve();
    const active=connection?.profiles.find(p=>p.id===connection.active), ready=active?.signed_in&&active?.plan_enabled&&!connection?.pending;
    dialog.innerHTML=`<div class="chat-heading"><div><span class="section-kicker">任意のオンライン機能</span><h2 id="chat-title">チャットで帳簿を編集</h2></div><button type="button" class="button small" data-chat="close" ${busy?'disabled':''}>閉じる</button></div>
      <p class="chat-privacy">OCR・取り込み時の自動分類は、これまでどおりPC内で行います。このチャットを送るときだけ、下の選択明細・登録済み科目の名前と説明・この会話をOpenAIへ送信します。PDF・画像・OCR本文は送りません。</p>
      <details class="chat-connection" ${ready?'':'open'}><summary>ChatGPTとの接続${active?'：'+escape(active.label):'（未接続）'}</summary>
      <p>対象のChatGPTプラン・アカウントで利用許可が必要です。プランの使用量・上限やクレジットの設定は、ChatGPT側で管理します。</p>
      ${connection?.profiles.length?`<label class="field"><span class="field-label">保存した接続先</span><select id="chat-account" ${busy?'disabled':''}><option value="">接続先を選択</option>${connection.profiles.map(p=>`<option value="${escape(p.id)}" ${connection.active===p.id?'selected':''}>${escape(p.label)}${p.signed_in?'':'（接続解除済み）'}</option>`).join('')}</select></label>`:''}
      <div class="import-actions"><button class="button" type="button" data-chat="connect" ${busy?'disabled':''}>Continue with ChatGPT</button>${active?`<button class="text-button" type="button" data-chat="reconnect" ${busy?'disabled':''}>${ready?'接続し直す':'利用を許可・接続し直す'}</button><button class="text-button" type="button" data-chat="disconnect" ${busy?'disabled':''}>この接続を解除</button>`:''}${connection?.pending?'<button class="text-button" type="button" data-chat="cancel-login">接続待ちをやめる</button>':''}<a href="https://chatgpt.com/settings/usage" target="_blank" rel="noreferrer">ChatGPTの使用量・接続設定 ↗</a>${connection?.pending&&loginUrl?`<a class="button" href="${escape(loginUrl)}" target="_blank" rel="noreferrer">ChatGPTの接続画面を開く ↗</a>`:''}</div><p class="field-hint" id="chat-connection-message">${escape(connection?.message||'APIキーは不要です。別のChatGPTアカウントも追加できます。')}</p>${connection?.pending&&loginUrl?'<p class="field-hint">ログイン画面が見つからない・開かないときは、上の「ChatGPTの接続画面を開く」を押してください。途中でやり直す場合は「接続待ちをやめる」を押します。</p>':''}</details>
      ${connection?.registration_pending&&!connection?.pending?`<p class="field-hint">未完了の接続登録があります。Continue with ChatGPTで同じ登録から再試行します。別の接続を作り直す場合は<button type="button" class="text-button" data-chat="reset-registration" ${busy?'disabled':''}>未完了の登録を破棄</button>を押してください。</p>`:''}
      <details class="chat-targets"><summary>送信する明細を確認（${targets.length}件・保存済みの内容）</summary><p class="field-hint">選択した明細の下記項目と識別IDを送ります。原本・OCR本文・選択していない明細・事業の概要は含みません。科目の選択肢として、登録済み科目名と説明も送信します。</p>${targets.map(r=>`<section><strong>${escape(r.draft.merchant||'支払先未入力')}</strong> · ${escape(r.id.slice(0,8))}<dl>${Object.entries(r.draft).map(([k,v])=>`<div><dt>${escape(labels[k]||k)}</dt><dd>${escape(shown(k,v))}</dd></div>`).join('')}</dl></section>`).join('')||'<p>経費帳で明細を選び、「選択分をチャットで編集」を押してください。1件だけなら明細の「チャットで編集」から開けます。</p>'}</details>
      <div class="chat-messages" aria-live="polite">${messages.map(m=>`<div class="chat-message ${m.role==='user'?'chat-user':''}"><strong>${m.role==='user'?'あなた':'Ryoshu'}</strong><p>${escape(m.text)}</p></div>`).join('')}</div>
      ${applyUnknown?'<p class="error-box" role="alert">保存されたか確認できません。変更が保存済みの可能性があります。「保存結果を確認」を押すと、同じ案を二重に保存せず結果を確認します。まだ保存されていない場合は、その案を適用します。結果が分かるまで案の取り消し・新しい依頼はできません。</p>':''}
      ${proposal?`<section class="chat-proposal"><h3>適用する前に、変更内容を確認</h3><p>適用すると${proposal.edits.length}件を「未確認」で保存します。確認済みの明細も再確認が必要になり、以前の確認済み仕訳は無効になります。変更履歴は残ります。</p>${proposal.edits.map(e=>`<section><strong>${escape(e.name)}</strong>${e.status==='confirmed'?'<span class="badge warning">確認済み → 未確認</span>':''}<dl>${Object.entries(e.changes).map(([field,change])=>`<div><dt>${escape(labels[field])}</dt><dd><del>${escape(shown(field,change.before))}</del><span aria-hidden="true"> → </span><ins>${escape(shown(field,change.after))}</ins></dd></div>`).join('')}</dl></section>`).join('')}<p class="field-hint">金額・科目・用途は原本と照合し、税務上の最終判断は税理士に確認してください。</p><div class="import-actions"><button type="button" class="button primary" data-chat="apply" ${busy?'disabled':''}>この変更案を未確認で保存</button><button type="button" class="text-button" data-chat="discard" ${busy?'disabled':''}>変更案を取り消す</button></div></section>`:''}
      <form id="chat-form"><label class="field"><span class="field-label">変更したいこと・質問</span><textarea id="chat-message" maxlength="4000" rows="3" placeholder="例：選んだ明細を通信費に変更して。事業割合は50%にして。" ${busy?'disabled':''}>${escape(input)}</textarea></label><label class="checkbox-line"><input id="chat-allow" type="checkbox" ${allowed?'checked':''} ${busy?'disabled':''}>選択明細とこの会話をOpenAIへ送信することを確認しました</label><label class="field"><span class="field-label">使うモデル</span><select id="chat-model" ${busy?'disabled':''}><option value="">モデルを選択</option>${models.map(m=>`<option value="${escape(m.id)}" ${m.id===model?'selected':''}>${escape(m.name)}</option>`).join('')}</select></label><div class="import-actions"><button type="button" class="button small" data-chat="models" ${!ready||busy?'disabled':''}>利用できるモデルを取得</button><button type="submit" class="button primary" ${!ready||!targets.length||busy?'disabled':''}>${busy?'処理しています…':'OpenAIに送って変更案を作る'}</button><button type="button" class="text-button" data-chat="reset" ${busy?'disabled':''}>会話を新しくする</button></div></form><p id="chat-error" role="alert"></p>`;
    if(applyUnknown){for(const el of dialog.querySelectorAll('button,input,select,textarea'))if(!['apply','close'].includes(el.dataset.chat))el.disabled=true;const apply=q('[data-chat="apply"]');if(apply)apply.textContent='保存結果を確認（未保存なら適用）';}
    if(active){const add=q('[data-chat="connect"]');add.textContent='別のChatGPT接続を追加';add.className='text-button';q('[data-chat="reconnect"]').className='button';}
  }
  function error(message){const el=q('#chat-error');if(el){el.className='error-box';el.textContent=message;}else bridge.notice(message,true);}
  function clearConversation(){session=null;proposal=null;messages=[];allowed=false;if(q('#chat-allow'))q('#chat-allow').checked=false;}
  async function refreshConnection(){const updated=await bridge.api('/chat/connection'),changed=JSON.stringify(updated)!==JSON.stringify(connection);if(connection&&updated.active!==connection.active&&!applyUnknown){clearConversation();models=[];model='';}connection=updated;if(changed)render();}
  function pollLogin(){
    clearTimeout(loginTimer);
    if(!dialog.open||!connection?.pending)return;
    loginTimer=setTimeout(async()=>{try{await refreshConnection();pollLogin();}catch(e){error(e.message);}},1500);
  }
  async function open(mode){
    if(busy)return;
    if(applyUnknown&&proposal){if(!dialog.open)dialog.showModal();render();return;}
    const rows=bridge.targets(mode);
    if(rows.length>50){bridge.notice('チャットで編集できるのは一度に50件までです。選択を減らしてください。',true);return;}
    if(bridge.dirty(rows.map(r=>r.id))){bridge.notice('入力中の変更を先に保存してください。チャットには保存済みの明細を送ります。',true);return;}
    // Opening again clears both the old conversation and its external-data consent.
    targets=rows.map(r=>({id:r.id,version:r.version,draft:{...r.draft}}));
    messages=[];session=null;proposal=null;input='';allowed=false;models=[];model='';connection=null;
    dialog.innerHTML='';dialog.showModal();render();
    try{await refreshConnection();pollLogin();}catch(e){error(e.message);}
  }
  async function login(reauthorize){
    const popup=window.open('about:blank','_blank');if(popup)popup.opener=null;
    try{
      clearConversation();models=[];model='';
      const result=await bridge.api('/chat/connect',{method:'POST',body:JSON.stringify({profile_id:reauthorize?connection.active:null,reconsent:!!reauthorize&&!connection.profiles.find(p=>p.id===connection.active)?.plan_enabled})});
      loginUrl=result.authorization_url;
      if(popup)popup.location.replace(result.authorization_url);
      await refreshConnection();pollLogin();
    }catch(e){if(popup)popup.close();throw e;}
  }
  async function send(event){
    event.preventDefault();if(busy||applyUnknown)return;preserve();
    if(!input.trim()){error('変更したいこと・質問を入力してください。');return;}
    if(!allowed){error('OpenAIへ送る内容を確認して、送信のチェックを入れてください。');return;}
    if(!model){error('「利用できるモデルを取得」から、モデルを選んでください。');return;}
    if(bridge.dirty(targets.map(r=>r.id))){error('未保存の入力があります。先に保存してから開き直してください。');return;}
    busy=true;proposal=null;render();
    try{
      const result=await bridge.api('/chat/propose',{method:'POST',body:JSON.stringify({rows:targets.map(({id,version})=>({id,version})),message:input.trim(),model,profile_id:connection.active,session,allow_external:true})});
      messages.push({role:'user',text:input.trim()},{role:'assistant',text:result.message});
      session=result.session;proposal=result.proposal_id?result:null;input='';
      if(q('#chat-message'))q('#chat-message').value='';
    }catch(e){try{await refreshConnection();}catch{}busy=false;const message=e.message;render();error(message);return;}
    finally{busy=false;}
    render();q('.chat-proposal, #chat-message')?.scrollIntoView({block:'nearest'});
  }
  async function apply(){
    if(!proposal||busy)return;
    if(bridge.dirty(proposal.edits.map(e=>e.id))){error('未保存の入力があります。先に保存し、最新の明細から依頼し直してください。');return;}
    const pending=proposal;busy=true;render();
    let result;
    try{result=await bridge.api('/chat/apply',{method:'POST',body:JSON.stringify({proposal_id:pending.proposal_id})});}
    catch(e){
      const rejected=e.status>=400&&e.status<500;
      applyUnknown=!rejected;
      if(rejected)clearConversation();
      try{await bridge.applied(pending.edits.map(edit=>edit.id));if(rejected){const fresh=await Promise.all(targets.map(r=>bridge.api('/receipts/'+r.id)));targets=fresh.map(r=>({id:r.id,version:r.version,draft:r.draft}));}}
      catch{if(rejected)targets=[];}
      busy=false;render();error(e.message+(rejected?' この編集案は適用していません。最新の明細を確認し、依頼し直してください。':''));return;
    }
    applyUnknown=false;clearConversation();messages.push({role:'assistant',text:`${result.changed.length}件の変更を未確認で保存しました。原本を確認してから確認済みにしてください。続ける場合は、新しい会話として送信内容を確認してください。`});
    try{
      await bridge.applied(result.changed);
      const fresh=await Promise.all(targets.map(r=>bridge.api('/receipts/'+r.id)));
      targets=fresh.map(r=>({id:r.id,version:r.version,draft:r.draft}));
    }catch(e){targets=[];messages.push({role:'assistant',text:'変更は保存済みですが、画面の更新に失敗しました。閉じて再読み込みしてください。'});}
    finally{busy=false;render();}
  }
  function setup(options){
    bridge=options;dialog=document.querySelector('#chat-dialog');
    dialog.addEventListener('cancel',event=>{if(busy){event.preventDefault();error('処理の完了を待ってください。');}else clearTimeout(loginTimer);});
    dialog.addEventListener('close',()=>clearTimeout(loginTimer));
    document.addEventListener('click',event=>{
      const button=event.target.closest('[data-chat-open]');if(button)open(button.dataset.chatOpen).catch(e=>bridge.notice(e.message,true));
    });
    dialog.addEventListener('change',async event=>{
      if(busy||applyUnknown)return;
      let locked=false,failure='';
      try{
        if(event.target.id==='chat-model'){model=event.target.value;session=null;proposal=null;messages=[];render();}
        if(event.target.id==='chat-account'&&event.target.value){const profile=event.target.value;busy=true;locked=true;render();await bridge.api('/chat/account',{method:'POST',body:JSON.stringify({profile_id:profile})});session=null;proposal=null;messages=[];models=[];model='';allowed=false;const consent=q('#chat-allow');if(consent)consent.checked=false;await refreshConnection();}
      }catch(e){failure=e.message;error(failure);}
      finally{if(locked){busy=false;render();if(failure)error(failure);}}
    });
    dialog.addEventListener('submit',send);
    dialog.addEventListener('click',async event=>{
      const action=event.target.closest('[data-chat]')?.dataset.chat;if(!action||busy)return;
      if(applyUnknown&&!['close','apply'].includes(action))return;
      let locked=false,failure='';
      try{
        preserve();
        if(action==='close'){dialog.close();return;}
        if(['connect','reconnect','models','disconnect','cancel-login','reset-registration'].includes(action)){busy=true;locked=true;render();}
        if(action==='connect'||action==='reconnect'){await login(action==='reconnect');return;}
        if(action==='cancel-login'){await bridge.api('/chat/cancel-login',{method:'POST'});await refreshConnection();return;}
        if(action==='reset-registration'){connection=await bridge.api('/chat/reset-registration',{method:'POST'});return;}
        if(action==='models'){models=(await bridge.api('/chat/models',{method:'POST'})).models;if(!models.length)throw new Error('この接続で利用できるモデルがありません。ChatGPTのプラン・利用許可を確認してください。');render();return;}
        if(action==='disconnect'){const result=await bridge.api('/chat/disconnect',{method:'POST',body:JSON.stringify({profile_id:connection.active})});session=null;proposal=null;messages=[];models=[];model='';allowed=false;q('#chat-allow').checked=false;await refreshConnection();bridge.notice(result.message,!result.revocation_confirmed);return;}
        if(action==='reset'){clearConversation();render();return;}
        if(action==='discard'){proposal=null;render();return;}
        if(action==='apply')await apply();
      }catch(e){failure=e.message;error(failure);}
      finally{if(locked){busy=false;render();if(failure)error(failure);}}
    });
  }
  function decorate(view){
    if(!bridge?.enabled)return;
    const toolbar=document.querySelector('.table-toolbar .toolbar-group');
    if(view==='ledger'&&toolbar&&!document.querySelector('[data-chat-open="selection"]'))toolbar.insertAdjacentHTML('beforeend','<button type="button" class="button small" data-chat-open="selection">選択分をチャットで編集</button>');
    if(view==='settings'&&!document.querySelector('#chat-settings'))document.querySelector('.settings-layout>div:last-child')?.insertAdjacentHTML('afterbegin','<section class="panel settings-card" id="chat-settings"><h2>ChatGPTでチャット編集（任意）</h2><p>ChatGPTに接続し、選んだ帳簿の明細だけを会話で修正できます。変更案は自分で確認してから適用します。利用時は選択明細と会話をOpenAIへ送信します。</p><button type="button" class="button" data-chat-open="settings">接続・利用設定</button></section>');
    decorateDetail(document.querySelector('#review-panel'));
  }
  function decorateDetail(panel){if(bridge?.enabled&&panel&&!panel.querySelector('[data-chat-open]'))panel.querySelector('.secondary-actions')?.insertAdjacentHTML('afterbegin','<button class="text-button" type="button" data-chat-open="detail">チャットで編集（任意）</button>');}
  return {setup,decorate,decorateDetail};
})();
