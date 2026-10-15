from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'node недоступен')
class VoiceButtonRegressionTests(unittest.TestCase):
    def test_missing_key_opens_notice_and_fresh_config_can_start_call(self):
        source = Path(__file__).resolve().parents[1] / 'factory/web_ui/static/voice.js'
        script = r'''
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const src=fs.readFileSync(process.argv[1],'utf8'),start=src.indexOf('  async function onButtonClick()'),end=src.indexOf('\n  //',start);
let ready=false,opened=0,started=0,phase='';const voice={active:false,starting:false,config:{enabled:false,reason:'no_key'}};
const ctx={voice,dom:{button:{hidden:false}},loadConfig:async()=>{voice.config={enabled:ready,reason:ready?'':'no_key'};},openPanel:()=>opened++,setPhase:(_,text)=>phase=text,tr:key=>key,start:async()=>started++,toggleMinimize:()=>{}};
vm.createContext(ctx);vm.runInContext(src.slice(start,end),ctx);
(async()=>{await ctx.onButtonClick();assert.equal(opened,1);assert.equal(phase,'voice.error.key_setup');assert.equal(ctx.dom.button.hidden,false);assert.equal(started,0);ready=true;await ctx.onButtonClick();assert.equal(started,1);})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        subprocess.run([shutil.which('node'), '-e', script, str(source)], check=True, timeout=15)
