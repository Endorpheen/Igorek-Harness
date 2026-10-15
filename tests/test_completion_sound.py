"""Completion audio follows persisted root-task success, never trace round endings."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_completion_sound_background_success_and_replay_guards():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js unavailable')
    module = Path(__file__).parents[1] / 'factory/web_ui/static/completion-sound.js'
    script = r'''
const assert = require('node:assert/strict');
const {create} = require(process.argv[1]);
(async () => {
 let background=true,plays=0,resumes=0;
 const sound=create({hidden:()=>background,load:async()=>new ArrayBuffer(1),makeContext:()=>({
  state:'running',destination:{},resume:async()=>{resumes++},decodeAudioData:async()=>({}),
  createBufferSource:()=>({connect(){},disconnect(){},start(){plays++}})
 }),report:e=>{throw e}});
 sound.unlock();
 const event=(kind,id,status,extra={})=>({kind,task_id:id,payload:{status,...extra}});
 await sound.observe(event('task.status','historical','ok'));
 assert.equal(plays,0);
 sound.watch('root');
 await sound.observe(event('model.round_response','root','ok'));
 await sound.observe(event('tool.result','root','ok'));
 await sound.observe(event('task.finish','root','ok'));
 await sound.observe(event('task.status','root','waiting_approval'));
 await sound.observe(event('subagent.finish','child','ok'));
 assert.equal(plays,0);
 await sound.observe(event('task.status','root','ok'));
 await sound.observe(event('task.status','root','ok'));
 sound.watch('root');
 await sound.observe(event('task.status','root','ok'));
 assert.equal(plays,1);
 background=false;
 sound.watch('foreground');
 await sound.observe(event('task.status','foreground','ok'));
 background=true;
 for (const status of ['fail','cancelled','timeout','dead_letter']) {
  sound.watch(status);await sound.observe(event('task.status',status,status));
 }
 await sound.observe(event('task.start','child','', {parent_task_id:'root',delegate_depth:1}));
 await sound.observe(event('task.status','child','ok'));
 assert.equal(plays,1);
 await sound.observe(event('task.submitted','new',''));
 await sound.observe(event('task.finish','new','ok'));
 await sound.observe(event('task.requeued','new',''));
 assert.equal(plays,1);
 await sound.observe(event('task.status','new','ok'));
 assert.equal(plays,2);
 assert.equal(resumes,1);
 let reports=0;
 const broken=create({makeContext:()=>{throw Error('no audio')},report:()=>reports++});
 broken.unlock();assert.equal(reports,1);
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run([node, '-e', script, str(module)], check=True, capture_output=True, text=True)


def test_desktop_focus_setting_and_late_disable_control_audio():
    module = Path(__file__).parents[1] / 'factory/web_ui/static/completion-sound.js'
    script = r'''
const assert = require('node:assert/strict');
const {create} = require(process.argv[1]);
(async () => {
 let enabled=true,background=true,plays=0,claims=0,delay;
 global.document={visibilityState:'visible'};
 global.IgorekDesktop={completionSoundEnabled:()=>enabled,isBackground:()=>background,
  claimCompletionSound:async()=>{claims++;return true}};
 const context={state:'running',destination:{},resume:async()=>{},decodeAudioData:async()=>({}),
  createBufferSource:()=>({connect(){},disconnect(){},start(){plays++}})};
 const sound=create({load:async()=>new ArrayBuffer(1),makeContext:()=>context,report:e=>{throw e}});
 sound.unlock();
 const finish=async id=>{sound.watch(id);await sound.observe({kind:'task.status',task_id:id,payload:{status:'ok'}})};
 await finish('background'); assert.equal(plays,1);assert.equal(claims,1);
 background=false;await finish('foreground');assert.equal(plays,1);
 background=true;enabled=false;await finish('disabled');assert.equal(plays,1);
 enabled=true;await finish('disabled');assert.equal(plays,1);
 const pending=create({hidden:()=>background,enabled:()=>enabled,makeContext:()=>context,
  load:()=>new Promise(resolve=>{delay=resolve}),report:e=>{throw e}});
 pending.unlock();pending.watch('pending');await Promise.resolve();
 const completion=pending.observe({kind:'task.status',task_id:'pending',payload:{status:'ok'}});
 enabled=false;delay(new ArrayBuffer(1));await completion;assert.equal(plays,1);
 enabled=true;
 const refocused=create({hidden:()=>background,makeContext:()=>context,load:async()=>new ArrayBuffer(1),
  claim:async()=>{background=false;return true},report:e=>{throw e}});
 refocused.unlock();refocused.watch('refocused');
 await refocused.observe({kind:'task.status',task_id:'refocused',payload:{status:'ok'}});assert.equal(plays,1);
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run([shutil.which('node'), '-e', script, str(module)], check=True, capture_output=True, text=True)
