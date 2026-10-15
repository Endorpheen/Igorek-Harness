'use strict';
const $=id=>document.getElementById(id);
const errors={login_not_verified:'Заверши вход на странице сайта, затем передай управление агенту.',vault_locked:'Разблокируй хранилище Игорька.',desktop_locked:'Разблокируй интерфейс Игорька.',operation_busy:'Дождись завершения предыдущего действия.',invalid_settings:'Проверь ограничения прохода.',invalid_url:'Введите адрес сайта, например google.com.',navigation_failed:'Страница не загрузилась. Повтори переход или обнови её.',browser_operation_failed:'Действие браузера не завершилось.'};
let busy=false,current=null,menuOpen=false,limitsDirty=false;
function render(value) {
  if(value.error){$('status').textContent=errors[value.error]||value.error;$('status').dataset.error='true';return;}
  current=value;
  if(document.activeElement!==$('address'))$('address').value=value.current_url&&value.current_url!=='about:blank'?value.current_url:value.open?'':value.settings?.feed_url||'';
  const signature=JSON.stringify(value.tabs||[]);
  if($('browser-tabs').dataset.signature!==signature){
    $('browser-tabs').dataset.signature=signature;$('browser-tabs').replaceChildren();
    for(const tab of value.tabs||[]){
      const wrapper=document.createElement('div');wrapper.className='browser-tab'+(tab.active?' active-tab':'');
      const select=document.createElement('button');select.className='tab-select';select.type='button';select.setAttribute('role','tab');select.setAttribute('aria-selected',String(tab.active));select.textContent=tab.title;select.title=tab.title;select.addEventListener('click',()=>action('tab_select',{id:tab.id}));
      const close=document.createElement('button');close.className='tab-close';close.type='button';close.textContent='×';close.title='Закрыть вкладку';close.setAttribute('aria-label','Закрыть вкладку '+tab.title);close.addEventListener('click',()=>action('tab_close',{id:tab.id}));
      wrapper.append(select,close);$('browser-tabs').append(wrapper);
    }
  }
  $('back').disabled=busy||!value.can_go_back;$('forward').disabled=busy||!value.can_go_forward;$('reload').disabled=busy||!value.open;
  document.body.classList.toggle('is-loading',Boolean(value.loading));
  $('pause').setAttribute('aria-checked',String(value.manual));$('resume').setAttribute('aria-checked',String(!value.manual));
  $('mode-label').textContent=value.manual?'Управляешь ты':'Управляет агент';
  if(!limitsDirty){$('seconds').value=value.settings?.max_seconds||600;$('posts').value=value.settings?.max_posts||100;}
  const notes=[];
  if(value.paused)notes.push(errors[value.paused]||({login_required:'Сайту нужен ручной вход.',challenge:'Сайт просит пройти проверку.'})[value.paused]||value.paused);
  if(value.job?.state==='running')notes.push('Идёт проход: '+value.job.count+' постов.');
  if(value.last_run)notes.push('Последний проход: '+new Date(value.last_run.finished_at).toLocaleString('ru-RU')+' · '+value.last_run.count+' постов.');
  $('status').textContent=notes.join('\n');
  $('status').dataset.error=String(Boolean(value.paused));
}
async function menu(open) {
  menuOpen=open;$('browser-menu').hidden=!open;$('menu-toggle').setAttribute('aria-expanded',String(open));
  try{await window.IgorekBrowser.action('menu',{open});}catch{}
  if(!open){$('limits').open=false;limitsDirty=false;if(current){$('seconds').value=current.settings?.max_seconds||600;$('posts').value=current.settings?.max_posts||100;}}
}
async function action(name,fields={}) {
  if(busy)return;
  busy=true;for(const id of ['back','forward','reload','pause','resume','limits-save'])$(id).disabled=true;
  try {
    const value=await window.IgorekBrowser.action(name,fields);if(name==='limits'&&!value.error)limitsDirty=false;render(value);
    if(!value.error&&['pause','resume'].includes(name))await menu(false);
    if(!value.error&&name==='limits'){$('status').textContent='Ограничения сохранены.';$('status').dataset.error='false';}
    if(value.error&&!menuOpen)await menu(true);
  }catch{$('status').textContent='Не удалось связаться с браузером Игорька.';$('status').dataset.error='true';await menu(true);}
  finally{busy=false;for(const id of ['pause','resume','limits-save'])$(id).disabled=false;if(current){$('back').disabled=!current.can_go_back;$('forward').disabled=!current.can_go_forward;$('reload').disabled=!current.open;}}
}
$('menu-toggle').addEventListener('click',()=>menu(!menuOpen));
window.addEventListener('igorek:browser-menu-close',()=>{menuOpen=false;limitsDirty=false;$('browser-menu').hidden=true;$('menu-toggle').setAttribute('aria-expanded','false');$('limits').open=false;});
document.addEventListener('pointerdown',event=>{if(menuOpen&&!$('browser-menu').contains(event.target)&&!$('menu-toggle').contains(event.target))menu(false);});
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='t'){event.preventDefault();action('tab_new');return;}if(event.key==='Escape'){menu(false);$('menu-toggle').focus();}});
for(const id of ['back','forward','reload','pause','resume'])$(id).addEventListener('click',()=>action(id));
$('tab-new').addEventListener('click',()=>action('tab_new'));
$('hide').addEventListener('click',()=>{menu(false);window.IgorekBrowser.action('hide').catch(()=>{});});
window.addEventListener('blur',()=>{if(menuOpen)menu(false);});
$('address-form').addEventListener('submit',event=>{event.preventDefault();const raw=$('address').value.trim();const url=raw.includes('://')?raw:'https://'+raw;$('address').blur();action('navigate',{url});});
$('address').addEventListener('focus',()=>{$('address').select();});
$('address').addEventListener('keydown',event=>{if(event.key==='Escape'){event.stopPropagation();$('address').value=current?.current_url||current?.settings?.feed_url||'';$('address').blur();}});
$('limits-form').addEventListener('submit',event=>{event.preventDefault();action('limits',{max_seconds:Number($('seconds').value),max_posts:Number($('posts').value)});});
$('limits-form').addEventListener('input',()=>{limitsDirty=true;});
async function poll(){try{const value=await window.IgorekBrowser.action('status');if(!busy)render(value);}catch{}}
poll();setInterval(poll,750);
