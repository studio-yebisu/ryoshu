const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const context={window:{}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/import.js'),'utf8'),context);
const readEntry=context.window.RyoshuImport.readEntry;
function file(name){return {isFile:true,file:resolve=>resolve({name})};}
function directory(batches){return {isDirectory:true,createReader(){let index=0;return {readEntries:resolve=>resolve(batches[index++]||[])};}};}
test('folder drop reads every batch beyond the browser first100 entries and nested folders',async()=>{
  const entries=Array.from({length:100},(_,i)=>file(i+'.pdf'));
  const result=await readEntry(directory([entries,[directory([[file('nested.pdf')]]),file('last.pdf')]]));
  assert.equal(result.length,102);
  assert.equal(result[100].name,'nested.pdf');
  assert.equal(result[101].name,'last.pdf');
});
test('unreadable entry rejects rather than silently losing part of a folder selection',async()=>{
  await assert.rejects(readEntry(directory([[file('good.pdf'),{isFile:true,file:(_,reject)=>reject(new Error('unreadable'))}]])),/unreadable/);
});
test('empty folders do not create phantom files',async()=>{assert.equal((await readEntry(directory([]))).length,0);});
