const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
function fixture({hold=false,duration=4}={}){
 const probes=[],timers=new Map();let timerId=0;
 const context={window:{},location:{href:'https://igorek.example/chat',origin:'https://igorek.example'},URL,queueMicrotask,setTimeout(fn){timers.set(++timerId,fn);return timerId},clearTimeout:id=>timers.delete(id),document:{createElement(tag){
  if(tag==='video'){
   const events=new Map(),probe={duration,videoWidth:100,videoHeight:60,muted:false,preload:'',playsInline:false,src:'',seeks:[],paused:0,loads:0,
    addEventListener:(name,fn)=>events.set(name,fn),removeEventListener:name=>events.delete(name),pause(){this.paused++},removeAttribute(name){if(name==='src')this.src=''},
    load(){this.loads++;if(this.src&&!hold)queueMicrotask(()=>events.get('loadedmetadata')?.())},fire:name=>events.get(name)?.()};
   let time=0;Object.defineProperty(probe,'currentTime',{get:()=>time,set:value=>{time=value;probe.seeks.push(value);if(!hold)queueMicrotask(()=>events.get('seeked')?.())}});probes.push(probe);return probe;
  }
  if(tag==='canvas'){let probe;const canvas={width:0,height:0,toDataURL:()=> 'data:image/jpeg;base64,decoded-frame',getContext:()=>({drawImage:p=>{probe=p},getImageData:()=>({data:new Uint8ClampedArray(canvas.width*canvas.height*4).fill(probe.currentTime>=1?150:0)})})};return canvas;}
  throw Error('Unexpected node '+tag);
 }}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../factory/web_ui/static/media-previews.js'),'utf8'),context);
 const player=(suffix='video')=>({tagName:'VIDEO',poster:'',src:'https://igorek.example/'+suffix,isConnected:true,currentTime:0,preload:'none',paused:true,readyState:0,loads:0,load(){this.loads++}});
 return{attach:context.window.IgorekMediaPreviews.attachVideoPoster,probes,timers,player,context};
}
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
test('video preview skips black intro, shares decoding and preserves playback position',async()=>{
 const f=fixture(),one=f.player(),two=f.player();f.attach(one);f.attach(two);assert.match(one.poster,/^data:image\/svg/);await flush();
 assert.match(one.poster,/^data:image\/jpeg/);assert.equal(one.poster,two.poster);assert.equal(f.probes.length,1);assert.deepEqual(f.probes[0].seeks,[0.4,1]);assert.equal(one.currentTime,0);assert.equal(one.preload,'metadata');assert.equal(one.loads,1);assert.equal(f.probes[0].muted,true);assert.equal(f.probes[0].src,'');assert.equal(f.timers.size,0);
});
test('cross-origin media and detached cards are never fetched',async()=>{
 const f=fixture(),external=f.player(),detached=f.player('old');external.src='https://other.example/video';detached.isConnected=false;f.attach(external);f.attach(detached);await flush();assert.equal(f.probes.length,0);
});
test('existing posters and other media types stay unchanged',async()=>{
 const f=fixture(),poster=f.player(),audio=f.player();poster.poster='https://igorek.example/custom.jpg';audio.tagName='AUDIO';f.attach(poster);f.attach(audio);f.attach(null);await flush();assert.equal(poster.poster,'https://igorek.example/custom.jpg');assert.equal(audio.poster,'');assert.equal(f.probes.length,0);
});
test('unsupported streams retain a visible fallback and release their decoder',async()=>{
 const f=fixture({duration:Infinity}),player=f.player();f.attach(player);await flush();assert.match(player.poster,/^data:image\/svg/);assert.equal(f.probes[0].src,'');assert.equal(f.probes[0].paused,1);assert.equal(f.timers.size,0);
});
test('timeout releases the source and cannot replace the fallback later',async()=>{
 const f=fixture({hold:true}),player=f.player();f.attach(player);await flush();assert.equal(f.probes.length,1);[...f.timers.values()][0]();await flush();f.probes[0].fire('seeked');assert.match(player.poster,/^data:image\/svg/);assert.equal(f.probes[0].src,'');assert.equal(f.timers.size,0);
});
test('only two videos decode at once while the remaining previews wait',async()=>{
 const f=fixture({hold:true});for(let i=0;i<6;i++)f.attach(f.player('video'+i));await flush();assert.equal(f.probes.length,2);assert.equal(f.timers.size,2);
 f.probes[0].fire('error');await flush();assert.equal(f.probes.length,3);assert.equal(f.timers.size,2);
});
test('a replaced card cannot receive an old asynchronous thumbnail',async()=>{
 const f=fixture({hold:true}),player=f.player();f.attach(player);await flush();const probe=f.probes[0];probe.fire('loadedmetadata');player.src='https://igorek.example/replaced';probe.currentTime=1;probe.fire('seeked');await flush();assert.match(player.poster,/^data:image\/svg/);
});

test('offscreen videos wait until visible and disconnect their observer',async()=>{
 const f=fixture();let listener,disconnected=0;f.context.IntersectionObserver=class{constructor(fn){listener=fn}observe(){}disconnect(){disconnected++}};
 const player=f.player();f.attach(player);await flush();assert.equal(f.probes.length,0);listener([{isIntersecting:false}]);await flush();assert.equal(f.probes.length,0);
 listener([{isIntersecting:true}]);await flush();assert.equal(f.probes.length,1);assert.equal(disconnected,1);assert.match(player.poster,/^data:image\/jpeg/);
});

test('previews never reload an already playing video',async()=>{
 const f=fixture(),player=f.player();player.paused=false;player.currentTime=2;f.attach(player);await flush();assert.equal(player.loads,0);assert.equal(player.currentTime,2);
});
