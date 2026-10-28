const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../../factory/web_ui/static/app.js'), 'utf8');
const start = source.indexOf('function slashCommandSuggestions(');
const end = source.indexOf('function showSlashCommandDialog(', start);
assert.ok(start >= 0 && end > start, 'slash command functions are present');
const ctx = vm.createContext({});
vm.runInContext(source.slice(start, end), ctx);
const names = (draft, goal = null, language = 'ru') =>
  Array.from(ctx.slashCommandSuggestions(draft, goal, language), item => item.command);

test('only real desktop commands are offered at the start of a message', () => {
  assert.deepEqual(names('/'), ['/help', '/status', '/goal', '/demo']);
  for (const value of ['', 'please /help', '/tmp/project', '/unknown', '/goalkeeper', '/goal do work', '/goal\nwork', '\n/help', '$skill']) {
    assert.deepEqual(names(value), [], value);
  }
  assert.deepEqual(names(' /HE'), ['/help']);
  assert.deepEqual(names('/st'), ['/status']);
});

test('goal actions match the actual current state and accept prefixes', () => {
  const active = {goal_id:'g', status:'active'};
  const paused = {goal_id:'g', status:'paused'};
  assert.deepEqual(names('/goal ', null), []);
  assert.deepEqual(names('/goal ', active), ['/goal pause', '/goal edit', '/goal clear']);
  assert.deepEqual(names('/goal r', paused), ['/goal resume']);
  assert.deepEqual(names('/goal p', paused), []);
  assert.deepEqual(names('/goal edit ', active), []);
  assert.deepEqual(names('/go', active), ['/goal', '/goal pause', '/goal edit', '/goal clear']);
});

test('descriptions follow the selected language', () => {
  const result = ctx.slashCommandSuggestions('/st', null, 'en');
  assert.equal(result[0].description, 'Show chat and context status');
});

test('/demo is discoverable by prefix in both languages alongside command help', () => {
  assert.deepEqual(names('/de'), ['/demo']);
  assert.deepEqual(names(' /DE'), ['/demo']);
  assert.deepEqual(names('run /demo'), []);
  assert.equal(ctx.slashCommandSuggestions('/de', null, 'ru')[0].description, 'Познакомиться с Игорьком');
  assert.equal(ctx.slashCommandSuggestions('/de', null, 'en')[0].description, 'Explore Igorek');
});

test('finishing a goal command preserves a new draft and a newly selected chat', async () => {
  const begin = source.indexOf('async function sendMessage(');
  const finish = source.indexOf('\n// Voice input:', begin);
  assert.ok(begin >= 0 && finish > begin);
  let resolveGoal;
  const state = {selected:'old-chat', session:{meta:{}}, attachments:[], sending:false};
  const ui = {message:{value:'/goal Work',style:{}}, send:{disabled:false}};
  const context = vm.createContext({state, ui, lang:'en', window:{IgorekComposerProfiles:{busy:()=>false}},
    handleGoalCommand:()=>new Promise(resolve=>{resolveGoal=resolve;}), toast:()=>{}});
  vm.runInContext(source.slice(begin, finish), context);
  const pending = context.sendMessage({preventDefault(){}});
  state.selected = 'new-chat'; ui.message.value = 'Keep this draft';
  resolveGoal(true);
  await pending;
  assert.equal(ui.message.value, 'Keep this draft');
  assert.equal(state.sending, false);
});

test('status never attributes another chat context or profile quota to the current chat', () => {
  class Node {
    constructor(tag, name, text) { this.tag=tag; this.className=name; this.textContent=text || ''; this.children=[]; }
    append(...items) { this.children.push(...items); }
    setAttribute() {}
    addEventListener() {}
    showModal() {}
  }
  const root = new Node('body');
  const state = {selected:'chat',plan:{session:'other',usage:{lastInput:900}},quota:{profile:'other',data:{weekly:{percent:90}}}};
  const context = vm.createContext({lang:'ru',state,document:{body:root},
    element:(...args)=>new Node(...args),modelContextWindow:()=>1000,formatCount:n=>String(n)});
  const begin=source.indexOf('function showSlashCommandDialog(');
  const finish=source.indexOf('function attachWorkspaceSlashMenu(',begin);
  vm.runInContext(source.slice(begin,finish),context);
  context.showSlashCommandDialog('/status','chat',{meta:{profile:'current',model:'fixture'}});
  const rows=root.children[0].children[1].children;
  assert.equal(rows[3].children[1].textContent,'Нет данных');
  assert.equal(rows[4].children[1].textContent,'Нет данных');
  state.plan.session='chat';state.quota.profile='current';
  context.showSlashCommandDialog('/status','chat',{meta:{profile:'current',model:'fixture'}});
  const actual=root.children[1].children[1].children;
  assert.match(actual[3].children[1].textContent,/100 из 1000/);
  assert.match(actual[4].children[1].textContent,/10%/);
});
