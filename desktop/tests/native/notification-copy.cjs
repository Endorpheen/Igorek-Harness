const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function verifyNotificationCopy({BrowserWindow, Menu, clipboard, root, output, appRoot}) {
  const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
  const until = async (predicate, message) => {
    const deadline = Date.now() + 5000;
    while (Date.now() < deadline) { if (await predicate()) return; await pause(40); }
    throw new Error(message);
  };
  const fixture = path.join(root, 'notification-copy.html');
  fs.writeFileSync(fixture, '<!doctype html><meta charset="utf-8"><style>' +
    fs.readFileSync(path.join(appRoot, 'window-chrome.css'), 'utf8') +
    '</style><body style="background:#111315"><nav class="app-rail"></nav>');
  const window = new BrowserWindow({width:1100,height:850,show:true,
    webPreferences:{sandbox:true,contextIsolation:true,nodeIntegration:false}});
  let menu, template;
  try {
    await window.loadFile(fixture);
    const initial = [{id:'fixture-incident',source:'Connection',detail:'Fixture notification text for copying.\nCode: NET.TRANSPORT_FAILURE',level:'error',read:false,time:Date.now(),count:1}];
    await window.webContents.executeJavaScript(`
      window.fixtureItems=${JSON.stringify(initial)};
      window.IgorekNotifications={list:async()=>fixtureItems,onChange:callback=>window.fixtureNotify=callback,
        read:async()=>fixtureItems=fixtureItems.map(item=>({...item,read:true})),clear:async()=>fixtureItems=[]};void 0;`);
    await window.webContents.executeJavaScript(fs.readFileSync(path.join(appRoot, 'notification-ui.js'), 'utf8'));
    await pause(100);
    await window.webContents.executeJavaScript("document.querySelector('#igorek-notifications-button').click()");
    await pause(100);
    await window.webContents.executeJavaScript("document.querySelector('.igorek-notification-row').open=true");
    await pause(100);
    const source = fs.readFileSync(path.join(appRoot, 'main.cjs'), 'utf8');
    const start = source.indexOf('function installContextMenu(');
    const end = source.indexOf('\nfunction ', start + 1);
    assert.ok(start >= 0 && end > start);
    const install = vm.runInNewContext(source.slice(start, end) + '; installContextMenu', {
      Menu:{buildFromTemplate:items => { template=items; menu=Menu.buildFromTemplate(items); return menu; }},
    });
    install(window);
    const point = await window.webContents.executeJavaScript(`(()=>{
      const node=document.querySelector('.igorek-notification-detail').firstChild;
      const begin=document.createRange();begin.setStart(node,0);begin.setEnd(node,1);
      const end=document.createRange();end.setStart(node,36);end.setEnd(node,37);
      const a=begin.getBoundingClientRect(),b=end.getBoundingClientRect();
      return {x1:Math.round(a.left+1),y:Math.round(a.top+a.height/2),x2:Math.round(b.right)};
    })()`);
    window.focus(); window.webContents.focus();
    window.webContents.sendInputEvent({type:'mouseDown',button:'left',clickCount:1,x:point.x1,y:point.y});
    for(let index=1;index<=15;index++) {
      window.webContents.sendInputEvent({type:'mouseMove',x:Math.round(point.x1+(point.x2-point.x1)*index/15),y:point.y});
      await pause(10);
    }
    window.webContents.sendInputEvent({type:'mouseUp',button:'left',clickCount:1,x:point.x2,y:point.y});
    await pause(100);
    const selected=await window.webContents.executeJavaScript('getSelection().toString()');
    assert.ok(selected.length>15,'Mouse drag did not select the notification');
    await window.webContents.executeJavaScript('fixtureNotify(fixtureItems)'); await pause(80);
    assert.equal(await window.webContents.executeJavaScript('getSelection().toString()'),selected,'Identical snapshot erased selection');
    const changed=[{...initial[0],id:'second-fixture',detail:'Another real notification'}, {...initial[0],detail:'Updated incident after copying',count:2}];
    await window.webContents.executeJavaScript(`fixtureItems=${JSON.stringify(changed)};fixtureNotify(fixtureItems);void 0;`);
    await pause(80);
    assert.equal(await window.webContents.executeJavaScript('getSelection().toString()'),selected,'New notification erased selection');
    assert.equal(await window.webContents.executeJavaScript("document.querySelector('.igorek-notification-badge').textContent"),'2');
    fs.writeFileSync(path.join(output,'notification-copy.png'),(await window.webContents.capturePage()).toPNG());
    window.webContents.sendInputEvent({type:'mouseMove',x:point.x1+30,y:point.y}); await pause(50);
    window.webContents.sendInputEvent({type:'mouseDown',button:'right',clickCount:1,x:point.x1+30,y:point.y});
    window.webContents.sendInputEvent({type:'mouseUp',button:'right',clickCount:1,x:point.x1+30,y:point.y});
    await until(()=>template?.some(item=>item.role==='copy'),'Native right-click menu has no Copy');
    await clipboard.writeText('fixture clipboard sentinel');
    const copy=menu.items.find(item=>item.role==='copy'); copy.click(copy,window,window.webContents);
    menu.closePopup(window); await pause(100);
    await until(async()=>await clipboard.readText()===selected,'Native Copy action copied different text');
    await clipboard.writeText('fixture shortcut sentinel');
    window.webContents.focus();
    window.webContents.sendInputEvent({type:'keyDown',keyCode:'C',modifiers:['control']});
    window.webContents.sendInputEvent({type:'keyUp',keyCode:'C',modifiers:['control']});
    await pause(100);
    await until(async()=>await clipboard.readText()===selected,'Ctrl+C copied different text');
    await window.webContents.executeJavaScript('getSelection().removeAllRanges()'); await pause(100);
    await until(()=>window.webContents.executeJavaScript("document.querySelectorAll('.igorek-notification-row').length===2"),'Pending list update did not resume');
    const latest=await window.webContents.executeJavaScript(`({count:document.querySelectorAll('.igorek-notification-row').length,
      text:document.querySelector('[data-id="fixture-incident"] .igorek-notification-detail').textContent,
      expanded:document.querySelector('[data-id="fixture-incident"]').open})`);
    assert.equal(latest.count,2); assert.equal(latest.text,'Updated incident after copying'); assert.equal(latest.expanded,true);
    await window.webContents.executeJavaScript(`(()=>{
      const range=document.createRange();range.selectNodeContents(document.querySelector('[data-id="fixture-incident"] .igorek-notification-detail'));
      getSelection().addRange(range);document.querySelectorAll('.igorek-notification-actions button')[0].click();
    })()`);
    await pause(100);
    await until(()=>window.webContents.executeJavaScript("document.querySelectorAll('.igorek-notification-row.unread').length===0"),'Explicit Mark all read was deferred');
    await window.webContents.executeJavaScript(`(()=>{
      const range=document.createRange();range.selectNodeContents(document.querySelector('[data-id="fixture-incident"] .igorek-notification-detail'));
      getSelection().addRange(range);document.querySelectorAll('.igorek-notification-actions button')[1].click();
    })()`);
    await pause(100);
    await until(()=>window.webContents.executeJavaScript("document.querySelectorAll('.igorek-notification-row').length===0"),'Explicit Clear was deferred');
    const result={status:'passed',mouseSelection:true,identicalSnapshotRetained:true,newSnapshotRetained:true,
      badgeStillLive:true,nativeContextMenuCopy:true,ctrlC:true,pendingUpdateApplied:true,expansionRetained:true,readImmediate:true,clearImmediate:true};
    fs.writeFileSync(path.join(output,'notification-copy.json'),JSON.stringify(result,null,2));
    return result;
  } finally {
    menu?.closePopup(window); window.destroy();
  }
}
module.exports={verifyNotificationCopy};
