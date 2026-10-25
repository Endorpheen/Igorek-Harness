const {test}=require('node:test'),assert=require('node:assert/strict');
const charts=require('../../factory/web_ui/static/charts.js');
const groups=require('../../factory/web_ui/static/tool-groups.js');
const sample=()=>({type:'bar',title:'Тест',labels:['Первый','Второй'],series:[{name:'A',values:[0,8]},{name:'B',values:[-2,4]}],unit:'баллы'});
const tool=(id,chartId,input=sample())=>({id,type:'tool',name:'render_chart',status:'done',input,result:JSON.stringify({ok:true,kind:'igorek.chart',schema_version:1,chart_id:chartId,revision:id})});
test('charts remain visible outside folded technical activity',()=>{
 const item=tool('a','chart');const output=groups.project([item,{id:'b',type:'tool',name:'Read'}]);assert.equal(output[0],item);assert.equal(output[1].type,'tool_group');
});
test('update keeps original DOM identity and old versions without mutating history',()=>{
 const first=tool('a','one'),next=tool('b','one');next.input=sample();next.input.title='Updated';const items=[first,{id:'text',type:'assistant',text:'Answer'},next];const before=JSON.stringify(items),out=charts.project(items);assert.equal(out.length,2);assert.equal(out[0].id,'a');assert.equal(out[0].input.title,'Updated');assert.equal(out[0].chart_revisions.length,2);assert.equal(JSON.stringify(items),before);
});
test('only confirmed successful versioned tool output becomes a chart',()=>{
 for(const result of ['',JSON.stringify({ok:false}),JSON.stringify({ok:true,kind:'other',schema_version:1,chart_id:'x'})])assert.equal(charts.decode({...tool('a','x'),result}),null);
 assert.equal(charts.decode(tool('a','x')).chart.title,'Тест');assert.equal(charts.isTool('mcp__igorek__render_chart'),true);
});
for(const input of [{...sample(),labels:[]},{...sample(),series:[{name:'A',values:[1,NaN]}]},{...sample(),type:'script'},{...sample(),series:[{name:'A',values:[null,2]}]},{...sample(),y_min:1}])test('invalid chart is rejected: '+JSON.stringify(input).slice(0,70),()=>assert.throws(()=>charts.validate(input)));
test('SVG export escapes all model text and supports all chart kinds',()=>{
 for(const type of ['bar','line','pie']){const raw=sample();raw.type=type;if(type==='pie')raw.series=[raw.series[0]];raw.title='<script>bad</script>';raw.labels[0]='<img onerror=bad>';const svg=charts.svg(charts.validate(raw));assert.ok(svg.startsWith('<svg'));assert.ok(!svg.includes('<script>'));assert.ok(svg.includes('&lt;script&gt;'));assert.ok(!svg.includes('NaN'));}
});
test('zero, negative and constant values retain finite SVG geometry',()=>{
 for(const values of [[0,0],[-5,-5],[-1,1]]){const raw=sample();raw.series=[{name:'A',values}];const svg=charts.svg(charts.validate(raw));assert.ok(!svg.includes('NaN'));assert.ok(!svg.includes('Infinity'));}
});
test('CSV escapes formula labels while keeping negative measurements numeric',()=>{
 const raw=sample();raw.labels[0]='=SUM(1,2)';raw.series[0].values[0]=-3;const csv=charts.csv(charts.validate(raw));assert.ok(csv.includes('"\'=SUM(1,2)"'));assert.ok(csv.includes('"-3"'));assert.ok(csv.includes('A (баллы)'));
});
test('unicode label limit counts code points across clients',()=>{const raw=sample();raw.labels[0]='🐙'.repeat(100);assert.equal(charts.validate(raw).labels[0],raw.labels[0]);raw.labels[0]+='🐙';assert.throws(()=>charts.validate(raw));});
test('stored call/result messages replay a chart after reopening the chat',()=>{
 const vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),source=fs.readFileSync(path.join(__dirname,'../../factory/web_ui/static/app.js'),'utf8');const context={t:k=>k};vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function sessionMessageItems('),source.indexOf('function compactionItem(')),context);
 const original=tool('saved','persisted'),messages=[{role:'tool',kind:'tool_call',task_id:'t',tool_use_id:'c',tool_name:'render_chart',tool_input:original.input},{role:'tool',kind:'tool_result',task_id:'t',tool_use_id:'c',tool_name:'render_chart',tool_output:original.result}];
 const items=context.sessionMessageItems(messages);assert.equal(items.length,1);assert.equal(charts.decode(items[0]).chart.title,'Тест');assert.equal(charts.decode(items[0]).chart.series[1].values[0],-2);assert.equal(groups.project(items)[0].type,'tool');
});

test('small nonzero observations remain nonzero in exported values',()=>{const raw=sample();raw.series[0].values[0]=0.00003;assert.match(charts.svg(charts.validate(raw)),/0[,.]00003/);});

test('standalone SVG retains title, legend and source',()=>{const raw=sample();raw.source='Fixture source';const output=charts.exportSvg(charts.validate(raw));assert.match(output,/>A<\/text>/);assert.match(output,/>B<\/text>/);assert.ok(output.includes('Fixture source'));assert.ok(output.includes('Тест'));});
