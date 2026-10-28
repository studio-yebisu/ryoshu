// Run the real navigation/poll functions with controlled responses; no browser.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
function between(start,end){
  const first=source.indexOf(start),last=source.indexOf(end,first);
  assert.ok(first>=0&&last>first,`Actual app function boundaries changed: ${start}`);
  return source.slice(first,last);
}
const actualFunctions=[
  between('function returnFromDetail(', 'function render()'),
  between('async function selectReceipt(', 'async function save('),
  between('async function poll(', "document.addEventListener('click',async event=>"),
].join('\n');

function deferred(){
  let resolve,reject;
  const promise=new Promise((success,failure)=>{resolve=success;reject=failure;});
  return {promise,resolve,reject};
}
function fixture(){
  const response=deferred(),requested=deferred(),renders=[],notices=[];
  const original={id:'old',version:1,status:'processing'};
  const fresh={old:{id:'old',version:3,status:'review'},other:{id:'other',version:1,status:'review'}};
  const state={view:'inbox',active:'old',detail:original,
    records:[{id:'old',version:2,status:'processing'},fresh.other],
    drafts:new Map(),detailOrigin:{view:'ledger',scroll:240}};
  let oldRequested=false;
  const context={state,detailRevision:0,window:{scrollY:240},
    history:{replaceState(){}},CSS:{escape:value=>value},
    api(route){
      assert.match(route,/^\/receipts\/(old|other)$/);
      if(route==='/receipts/old'&&!oldRequested){oldRequested=true;requested.resolve();return response.promise;}
      return Promise.resolve(fresh[route.split('/').at(-1)]);
    },
    loadReceipts:async()=>{},renderProcessing(){},renderPendingCount(){},
    busyRecord:row=>['queued','processing'].includes(row?.status),
    renderQueue(){},refreshLedgerDisplay(){},renderHome(){},renderResults(){},schedulePoll(){},
    toast(message,error){notices.push({message,error});},
    $(selector){return selector==='#review-dialog'?{open:false}:{};},
    focusPage(top=0){context.window.scrollY=top;},
    render(){renders.push({view:state.view,active:state.active,detail:state.detail?.id});},
  };
  vm.createContext(context);
  vm.runInContext(actualFunctions,context);
  return {context,state,original,fresh,response,requested,renders,notices};
}

test('late poll response cannot replace a different detail opened after returning to CSV',async()=>{
  const f=fixture(),pending=f.context.poll();
  await f.requested.promise;
  f.context.returnFromDetail();
  assert.equal(f.state.view,'ledger');
  await f.context.selectReceipt('other');
  const rendered=f.renders.length;
  f.response.resolve({id:'old',version:2,status:'review'});
  await pending;
  assert.equal(f.state.view,'inbox');assert.equal(f.state.active,'other');
  assert.equal(f.state.detail,f.fresh.other);
  assert.equal(f.renders.length,rendered,'discarded poll must not render the old detail');
});

test('reopening the same receipt invalidates an older poll through the navigation revision',async()=>{
  const f=fixture(),pending=f.context.poll();
  await f.requested.promise;
  f.context.returnFromDetail();await f.context.selectReceipt('old');
  assert.equal(f.state.detail.version,3);
  const rendered=f.renders.length;
  f.response.resolve({id:'old',version:2,status:'review'});
  await pending;
  assert.equal(f.state.active,'old');assert.equal(f.state.detail,f.fresh.old);
  assert.equal(f.renders.length,rendered);
});

test('a draft created while polling is awaiting detail keeps its input and current detail',async()=>{
  const f=fixture(),pending=f.context.poll();
  await f.requested.promise;
  const typed={version:1,draft:{purpose:'入力中の用途'}};
  f.state.drafts.set('old',typed);
  f.response.resolve({id:'old',version:2,status:'review'});
  await pending;
  assert.equal(f.state.detail,f.original);
  assert.equal(f.state.drafts.get('old'),typed);
  assert.equal(f.renders.length,0,'poll must not replace the form after typing starts');
});

test('an unchanged active receipt without a draft receives and renders a valid poll refresh',async()=>{
  const f=fixture(),pending=f.context.poll();
  await f.requested.promise;
  const updated={id:'old',version:2,status:'review'};
  f.response.resolve(updated);await pending;
  assert.equal(f.state.detail,updated);assert.equal(f.state.active,'old');
  assert.deepEqual(f.renders,[{view:'inbox',active:'old',detail:'old'}]);
  assert.equal(f.notices.length,0);
});

test('failure of an old detail request does not disturb the newer active detail',async()=>{
  const f=fixture(),pending=f.context.poll();
  await f.requested.promise;
  f.context.returnFromDetail();await f.context.selectReceipt('other');
  const rendered=f.renders.length;
  f.response.reject(new Error('controlled old request failure'));await pending;
  assert.equal(f.state.active,'other');assert.equal(f.state.detail,f.fresh.other);
  assert.equal(f.renders.length,rendered);
  assert.equal(f.notices.length,1);assert.equal(f.notices[0].error,true);
});

// Append to tests/detail-navigation.test.cjs (after the existing tests). Uses that file's `between`,
// `test`, `assert`, `vm`. Fails on the current source if H1 is real; passes with candidate-fix-app.js.patch.

const ledgerFocusFunctions=[
  between('const $ = (selector', '\n'),
  between('let toastTimer, pollTimer, searchTimer;', '\n'),
  between('function focusPage(', 'function render()'),
  between('function updateLedgerTable(', 'function renderLedger('),
  between('async function selectReceipt(', 'async function save('),
  between("document.addEventListener('input',event=>{if(event.target.id==='ledger-search')", '\n'),
].join('\n');

function ledgerFocusFixture(){
  const timers=[],listeners={},doc={activeElement:null};
  let buttons=[];
  const body={dataset:{},focus(){doc.activeElement=body;}};
  const plain=()=>({textContent:'',disabled:false,checked:false,scrollTop:0,scrollLeft:0,focus(){doc.activeElement=this;}});
  const rows={
    set innerHTML(_){
      for(const b of buttons)b.isConnected=false;
      buttons=[{dataset:{openReceipt:'r1'},isConnected:true,focus(){doc.activeElement=this;}}];
      if(doc.activeElement?.isConnected===false)doc.activeElement=body;
    },
    contains:el=>buttons.includes(el),
    querySelector:sel=>buttons.find(b=>sel===`[data-open-receipt="${b.dataset.openReceipt}"]`)||null,
  };
  const elements={'#ledger-rows':rows,'#ledger-table':plain(),'#ledger-count':plain(),'#ledger-page-controls':plain(),'#select-ledger-all':plain(),'#page-title':plain()};
  Object.assign(doc,{activeElement:body,querySelector:sel=>elements[sel]||rows.querySelector(sel),querySelectorAll:()=>[],
    addEventListener(type,fn){(listeners[type]=listeners[type]||[]).push(fn);}});
  const record={id:'r1',version:1,status:'review'};
  const state={view:'ledger',records:[record],active:null,detail:null,detailOrigin:null,ledgerSearch:'',ledgerPage:0,searchComposing:false,selected:new Map()};
  const win={scrollY:0,scrollTo({top}){win.scrollY=top;}};
  const context={state,document:doc,window:win,CSS:{escape:v=>v},history:{replaceState(){}},detailRevision:0,
    setTimeout(fn,ms){timers.push({fn,ms});return timers.length;},clearTimeout(){},
    api:async()=>({...record}),
    ledgerSelection:()=>({all:[record],filtered:[record],pages:1,rows:[record],selectable:[]}),
    ledgerBody:()=>'',ledgerCountLabel:()=>'1',ledgerPaging:()=>'',
    render(){if(state.view==='ledger')rows.innerHTML='';}};
  vm.createContext(context);
  vm.runInContext(ledgerFocusFunctions,context);
  return {context,state,win,timers,doc,listeners};
}

test('a search refresh that fires after returning from a detail keeps the origin row focused',async()=>{
  const f=ledgerFocusFixture();
  f.context.render();
  f.listeners.input.forEach(fn=>fn({target:{id:'ledger-search',value:'fictional',closest:()=>null},isComposing:false}));
  assert.deepEqual(f.timers.map(t=>t.ms),[180],'search refresh must still be debounced');
  f.win.scrollY=240;
  await f.context.selectReceipt('r1');
  f.context.returnFromDetail();
  assert.equal(f.state.view,'ledger');assert.equal(f.win.scrollY,240);
  assert.equal(f.doc.activeElement.dataset.openReceipt,'r1');
  f.timers.splice(0).forEach(t=>t.fn());   // the pending refresh fires now, after the return
  assert.equal(f.doc.activeElement.isConnected,true,'focus fell off the rebuilt ledger row');
  assert.equal(f.doc.activeElement.dataset.openReceipt,'r1');
  assert.equal(f.win.scrollY,240,'outer scroll must stay where the return put it');
});
