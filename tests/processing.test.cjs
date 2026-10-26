// Pure state tests for the processing view; real DOM/HTTP checks live in the browser smoke.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const context={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/processing.js'),'utf8'),context);
const {processingState,summarize,createTracker}=context.window.RyoshuProcessing;
const row=(id,status='queued',extra={})=>({id,status,original_name:id+'.pdf',version:1,updated_at:'2026-10-24T03:00:00Z',...extra});
const imported=(...ids)=>({imported:ids.map(id=>({id,name:id+'.pdf',duplicate:false}))});
const fresh=(time=1000,importing=false)=>({fetchedAt:time,importing});
const members=set=>Array.from(set).sort();

test('completed OCR, partial OCR, failed OCR and human confirmation remain distinct',()=>{
  assert.equal(processingState(row('a','review')),'complete');
  assert.equal(processingState(row('a','review',{error:'AI unavailable'})),'partial');
  assert.equal(processingState(row('a','error')),'failed');
  assert.equal(processingState(row('a','confirmed')),'confirmed');
  assert.equal(processingState(row('a','excluded')),'excluded');
  assert.equal(processingState(undefined),'syncing');
});

test('reload captures only remaining work, excluding old confirmed, complete and excluded rows',()=>{
  const tracker=createTracker();
  const summary=tracker.observe([row('old','confirmed'),row('old-review','review'),row('removed','excluded'),row('active','processing'),row('waiting')],fresh());
  assert.equal(summary.scope,'remaining');assert.equal(summary.total,2);assert.equal(summary.completed,0);
  assert.equal(summary.active,1);assert.equal(summary.waiting,1);assert.equal(summary.processed,0);
  assert.deepEqual(members(summary.newlyCompleted),[]);
});

test('fast completion before first list refresh counts newly saved IDs and animates once',()=>{
  const tracker=createTracker();tracker.begin();tracker.imported(imported('new'));
  let summary=tracker.observe([row('old','confirmed'),row('new','review')],fresh());
  assert.equal(summary.total,1);assert.equal(summary.completed,1);assert.equal(summary.processed,1);
  assert.deepEqual(members(summary.newlyCompleted),['new']);
  summary=tracker.observe([row('old','confirmed'),row('new','review')],fresh(2000));
  assert.deepEqual(members(summary.newlyCompleted),[]);
});

test('page IDs are counted separately while duplicates and repeated saved callbacks do not inflate totals',()=>{
  const tracker=createTracker();tracker.begin();
  const result={imported:[{id:'page1',duplicate:false},{id:'page2',duplicate:false},{id:'old',duplicate:true}]};
  tracker.imported(result);tracker.imported(result);
  const summary=tracker.observe([row('page1'),row('page2'),row('old','confirmed')],fresh());
  assert.equal(summary.total,2);assert.equal(summary.waiting,2);assert.equal(summary.completed,0);
});

test('saved IDs waiting for a list response do not invent queued or completed server states',()=>{
  const tracker=createTracker();tracker.begin();tracker.imported(imported('new'));
  const summary=tracker.observe([],fresh());
  assert.equal(summary.total,1);assert.equal(summary.syncing,1);assert.equal(summary.waiting,0);
  assert.equal(summary.processed,0);assert.equal(summary.next[0].kind,'syncing');
});

test('adding a batch while processing retains work; a new batch after completion resets the cohort',()=>{
  const tracker=createTracker();tracker.begin();tracker.imported(imported('a'));
  tracker.observe([row('a','processing')],fresh());
  tracker.begin();tracker.imported(imported('b'));
  assert.equal(tracker.observe([row('a','processing'),row('b')],fresh(2000)).total,2);
  tracker.observe([row('a','review'),row('b','review')],fresh(3000));
  tracker.begin();tracker.imported(imported('c'));
  const summary=tracker.observe([row('a','review'),row('b','review'),row('c')],fresh(4000));
  assert.equal(summary.total,1);assert.equal(summary.completed,0);assert.equal(summary.waiting,1);
});

test('uploading batches do not reset between individually completed fast files',()=>{
  const tracker=createTracker();tracker.begin();tracker.imported(imported('a'));
  tracker.observe([row('a','review')],fresh(1000,true));
  tracker.imported(imported('b'));
  const summary=tracker.observe([row('a','review'),row('b','processing')],fresh(2000,true));
  assert.equal(summary.total,2);assert.equal(summary.completed,1);assert.equal(summary.active,1);
});

test('pausing imports preserves the unfinished cohort after saved work completes and resume appends new IDs',()=>{
  const tracker=createTracker();tracker.begin();tracker.imported(imported('a'));
  let summary=tracker.observe([row('a','processing')],{...fresh(1000,true),paused:true});
  assert.equal(summary.paused,true);assert.equal(summary.active,1);
  summary=tracker.observe([row('a','review')],{...fresh(2000,true),paused:true});
  assert.equal(summary.paused,true);assert.equal(summary.importing,true);
  assert.equal(summary.busy,0);assert.equal(summary.total,1);assert.equal(summary.completed,1);
  tracker.imported(imported('b'));
  summary=tracker.observe([row('a','review'),row('b','processing')],fresh(3000,true));
  assert.equal(summary.paused,false);assert.equal(summary.total,2);
  assert.equal(summary.completed,1);assert.equal(summary.active,1);
  summary=tracker.observe([row('a','review'),row('b','review')],{...fresh(4000),paused:true});
  assert.equal(summary.paused,false);assert.equal(summary.importing,false);assert.equal(summary.completed,2);
});

test('OCR to model stage keeps one active job and does not claim intermediate completion',()=>{
  const tracker=createTracker();
  tracker.observe([row('a','processing',{stage:'ocr'})],fresh());
  const summary=tracker.observe([row('a','processing',{stage:'model',updated_at:'2026-10-24T03:00:20Z'})],fresh(2000));
  assert.equal(summary.total,1);assert.equal(summary.active,1);assert.equal(summary.completed,0);
  assert.equal(summary.current.row.stage,'model');assert.equal(summary.current.row.updated_at,'2026-10-24T03:00:20Z');
  assert.deepEqual(members(summary.newlyCompleted),[]);
});

test('partial and failed jobs count as handled attention, never successful reads; retry returns to work',()=>{
  const tracker=createTracker();tracker.begin();tracker.imported(imported('a','b','c'));
  const rows=[row('a','review'),row('b','review',{error:'AI stopped'}),row('c','error')];
  let summary=tracker.observe(rows,fresh());
  assert.equal(summary.completed,1);assert.equal(summary.attention,2);assert.equal(summary.processed,3);
  assert.deepEqual(members(summary.newlyCompleted),['a']);
  rows[2]=row('c','processing',{version:2,stage:'ocr'});
  summary=tracker.observe(rows,fresh(2000));
  assert.equal(summary.total,3);assert.equal(summary.processed,2);assert.equal(summary.active,1);assert.equal(summary.attention,1);
  rows[2]=row('c','review',{version:3});
  summary=tracker.observe(rows,fresh(3000));
  assert.equal(summary.completed,2);assert.deepEqual(members(summary.newlyCompleted),['c']);
});

test('human confirmation or editing a completed row does not replay read-completion feedback',()=>{
  const tracker=createTracker();tracker.begin();tracker.imported(imported('a'));
  tracker.observe([row('a','review',{version:2})],fresh());
  let summary=tracker.observe([row('a','confirmed',{version:3})],fresh(2000));
  assert.equal(summary.rows[0].kind,'confirmed');assert.deepEqual(members(summary.newlyCompleted),[]);
  summary=tracker.observe([row('a','review',{version:4})],fresh(3000));
  assert.deepEqual(members(summary.newlyCompleted),[]);
});

test('a fast retry clearing an error between polls animates while manual confirmation of a partial read does not',()=>{
  const tracker=createTracker();tracker.begin();tracker.imported(imported('a','b'));
  tracker.observe([row('a','error',{error:'read failed'}),row('b','review',{error:'AI failed'})],fresh());
  const summary=tracker.observe([row('a','review',{version:3}),row('b','confirmed',{version:2,error:'AI failed'})],fresh(2000));
  assert.deepEqual(members(summary.newlyCompleted),['a']);
});

test('dismissed finished results remain dismissed until actual new work or retry appears',()=>{
  const tracker=createTracker();tracker.begin();tracker.imported(imported('a'));
  tracker.observe([row('a','error')],fresh());tracker.dismiss();
  assert.equal(tracker.observe([row('a','error')],fresh(2000)).dismissed,true);
  assert.equal(tracker.observe([row('a','processing',{version:2})],fresh(3000)).dismissed,false);
  tracker.observe([row('a','review',{version:3})],fresh(4000));tracker.dismiss();
  const summary=tracker.observe([row('a','review',{version:3}),row('new')],fresh(5000));
  assert.equal(summary.dismissed,false);assert.equal(summary.total,1);assert.equal(summary.scope,'remaining');
});

test('failed connections keep last known status until a newer successful response',()=>{
  const tracker=createTracker();tracker.observe([row('a','processing')],fresh(1000));tracker.connectionFailed();
  assert.equal(tracker.last.stale,true);
  assert.equal(tracker.observe([row('a','processing')],fresh(1000)).stale,true);
  assert.equal(tracker.observe([row('a','review')],fresh(2000)).stale,false);
});

test('older failures retain a retry affordance without inflating the current batch denominator',()=>{
  const tracker=createTracker();tracker.begin();tracker.imported(imported('new'));
  const summary=tracker.observe([row('old','error'),row('new')],fresh());
  assert.equal(summary.total,1);assert.equal(summary.attention,0);assert.equal(summary.otherAttention,1);
  const reloaded=createTracker().observe([row('old','error')],fresh());
  assert.equal(reloaded.scope,'attention');assert.equal(reloaded.attention,1);
});

test('recent history and next queue are bounded and final state does not suddenly disappear',()=>{
  const tracker=createTracker();tracker.begin();tracker.imported(imported(...Array.from({length:20},(_,i)=>'r'+i)));
  const rows=Array.from({length:20},(_,i)=>row('r'+i,i<10?'review':'queued'));
  let summary=tracker.observe(rows,fresh());
  assert.equal(summary.recent.length,6);assert.equal(summary.next.length,4);
  summary=tracker.observe(rows.map(item=>({...item,status:'review'})),fresh(2000));
  assert.equal(summary.total,20);assert.equal(summary.processed,20);assert.equal(summary.busy,0);assert.equal(summary.dismissed,false);
});

function trackerWithClock(start){
  let now=start;
  const timed={window:{},Date:class extends Date{static now(){return now;}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/processing.js'),'utf8'),timed);
  return {tracker:timed.window.RyoshuProcessing.createTracker(),isStale:timed.window.RyoshuProcessing.isStale,setTime(value){now=value;}};
}

test('a new batch after idle clears the old failure and gets bounded first-response grace without inventing a fetch',()=>{
  const {tracker,isStale,setTime}=trackerWithClock(1000);
  tracker.begin();tracker.imported(imported('old'));
  tracker.observe([row('old','review')],fresh(1000));
  tracker.connectionFailed();
  assert.equal(tracker.last.stale,true);
  setTime(100000);
  tracker.begin();tracker.imported(imported('new'));
  const summary=tracker.observe([row('old','review')],fresh(1000,true));
  assert.equal(summary.total,1);assert.equal(summary.syncing,1);
  assert.equal(summary.fetchedAt,1000,'starting a batch is not a successful server response');
  assert.equal(summary.stale,false);
  assert.equal(isStale(summary,100000),false);
  assert.equal(isStale(summary,109000),false);
  assert.equal(isStale(summary,109001),true,'waiting for the first response must not hide a real timeout indefinitely');
});

test('adding to an ongoing stale batch does not renew its response grace or hide a connection failure',()=>{
  const {tracker,isStale,setTime}=trackerWithClock(1000);
  tracker.begin();tracker.imported(imported('active'));
  tracker.observe([row('active','processing')],fresh(1000,true));
  setTime(11000);
  assert.equal(isStale(tracker.last),true);
  tracker.begin();tracker.imported(imported('added'));
  let summary=tracker.observe([row('active','processing')],fresh(1000,true));
  assert.equal(summary.total,2);assert.equal(isStale(summary),true);
  assert.equal(summary.freshnessStartedAt,1000);
  tracker.connectionFailed();
  tracker.begin();
  summary=tracker.observe([row('active','processing')],fresh(1000,true));
  assert.equal(summary.stale,true);assert.equal(isStale(summary),true);
});

test('a real connection failure overrides a new-batch grace and only a newer response clears it',()=>{
  const {tracker,isStale,setTime}=trackerWithClock(100000);
  tracker.begin();tracker.imported(imported('new'));
  tracker.observe([],fresh(1000,true));
  assert.equal(isStale(tracker.last),false);
  tracker.connectionFailed();
  assert.equal(isStale(tracker.last),true);
  assert.equal(tracker.observe([],fresh(1000,true)).stale,true);
  setTime(100500);
  const summary=tracker.observe([row('new','processing')],fresh(100500,true));
  assert.equal(summary.stale,false);assert.equal(summary.fetchedAt,100500);
  assert.equal(isStale(summary),false);
  assert.equal(isStale(summary,109501),true);
});
