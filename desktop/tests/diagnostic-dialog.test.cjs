const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {join}=require('node:path');
const vm=require('node:vm');

test('a closed report can reopen before the asynchronous close event arrives',async()=>{
  const nodes=[],closed=[];
  class Element {
    constructor(tag){this.tagName=tag;this.children=[];this.events={};this.isConnected=false;this.open=false;}
    append(...children){this.children.push(...children);for(const child of children)child.isConnected=true;}
    setAttribute(){}
    addEventListener(name,callback){this.events[name]=callback;}
    focus(){}
    showModal(){this.open=true;}
    close(){this.open=false;closed.push(()=>this.events.close?.());}
    remove(){this.isConnected=false;}
  }
  const document={documentElement:{lang:'en'},createElement:tag=>new Element(tag),getElementById:id=>nodes.find(n=>n.isConnected&&n.id===id)||null,
    body:{append(node){nodes.push(node);node.isConnected=true;}}};
  const context={module:{exports:{}},require:()=>require('../error-diagnostics.js'),document,window:{}};
  vm.runInNewContext(readFileSync(join(__dirname,'../diagnostic-support.js'),'utf8'),context);
  const support=context.module.exports;
  await support.open({report:{}});
  const first=document.getElementById('igorek-diagnostic-support');assert.equal(first.open,true);
  first.close();
  await support.open({report:{}});
  const reopened=document.getElementById('igorek-diagnostic-support');
  assert.notEqual(reopened,first);assert.equal(reopened.open,true);
  closed.forEach(callback=>callback());
  assert.equal(document.getElementById('igorek-diagnostic-support'),reopened);
  assert.equal(reopened.isConnected,true);
});
