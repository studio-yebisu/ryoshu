// Counterexamples from review: legacy custom names may equal newly seeded codes.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const source=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
const seed=JSON.parse(fs.readFileSync(path.join(root,'data/account-catalog.json'),'utf8'));
const start=source.indexOf('function selectableAccounts('),end=source.indexOf('function accountCategories(');
assert.ok(start>=0&&end>start);
const context={state:{config:{accounts:seed.accounts.filter(a=>a.selectable)}}};
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
test('existing custom formal names survive newly introduced seed-code collisions',()=>{
  for(const [name,kind] of [['E129','expense'],['A106','asset']]){
    const custom={code:'ULEGACY'+name,name,kind,selectable:true,aliases:[],category_id:kind==='asset'?'assets':'other'};
    context.state.config.accounts.push(custom);
    assert.equal(context.findAccount(name).code,custom.code);
    assert.equal(context.findAccount(custom.code).name,name);
  }
});
test('new formal accounts override legacy aliases while old canonical names remain intact',()=>{
  for(const [name,code] of [['事務用品費','E129'],['販売促進費','E130'],['外注費','E131'],['支払利息','E132'],['リース料','E133']])assert.equal(context.findAccount(name).code,code);
  assert.equal(context.findAccount('消耗品費').code,'E101');
  assert.equal(context.findAccount('電話代').name,'通信費');
  assert.equal(context.findAccount(' 事務用品費 ').name,'事務用品費');
  assert.equal(context.findAccount(''),undefined);
});
