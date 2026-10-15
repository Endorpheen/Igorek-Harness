"""Approval renderers keep the actual action visible beside model rationale."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('relative', ['factory/web_ui/static/app.js', 'factory/web_ui/static/voice.js',
                                     'android/app/src/main/assets/voice/voice.js'])
def test_approval_rationale_never_replaces_the_action(relative):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for renderer acceptance')
    source = Path(__file__).resolve().parents[1] / relative
    script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[1],'utf8');
function element(tag,cls='',text=''){return {tag,cls,textContent:text,children:[],style:{},classList:{toggle(){}},append(...nodes){this.children.push(...nodes)},replaceChildren(){this.children=[]},addEventListener(){},get childElementCount(){return this.children.length}}}
const approval={tool:'Bash',target:'rm -rf /workspace/fixture',reason:'Read-only explanation',request_id:'request-fixture',input:{command:'rm -rf /workspace/fixture'}};
const context={element,makeEl:element,t:key=>key,tr:key=>key,stringify:JSON.stringify,appendApprovalOrigin(){},registerTimelineUpdater:card=>card,decideApproval(){},resolveApproval(){},publishCallState(){},voice:{pending:new Map(),approval},dom:{task:element('div')}};
vm.createContext(context);
const app=process.argv[1].endsWith('/app.js');
const start=source.indexOf(app?'function renderApproval(item) {':'  function renderTask() {');
const end=source.indexOf(app?'\nasync function decideApproval':'\n  function ',start+1);
assert.ok(start>=0&&end>start);vm.runInContext(source.slice(start,end),context);
const text=node=>[node.textContent,...node.children.map(text)].join('\n');
for(const target of ['rm -rf /workspace/fixture','']){
 approval.target=target;
 const rendered=app?context.renderApproval(approval):(context.renderTask(),context.dom.task);
 const value=text(rendered);
 assert.ok(value.includes(approval.input.command));assert.ok(value.includes(approval.reason));
 assert.ok(value.indexOf(approval.input.command)<value.indexOf(approval.reason));
}
'''
    result = subprocess.run([node, '-e', script, str(source)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
