(function(global){
  'use strict';
  class Model {
    constructor(){this.tabs=[];this.activeId='';this.serial=0;this.open('start');}
    get active(){return this.tabs.find(tab=>tab.id===this.activeId)||null;}
    open(kind,key='',title=''){
      let tab=kind!=='start'&&kind!=='browser'&&kind!=='chat'?this.tabs.find(item=>item.kind===kind&&item.key===key):null;
      if(!tab){if(this.tabs.length>=32)throw Error('Можно открыть до 32 вкладок.');tab={id:'workspace-'+(++this.serial),kind,key,title};this.tabs.push(tab);}
      this.activeId=tab.id;return tab;
    }
    select(id){if(this.tabs.some(tab=>tab.id===id))this.activeId=id;return this.active;}
    move(id,index){
      const from=this.tabs.findIndex(tab=>tab.id===id);
      if(from<0||!Number.isInteger(index)||index<0||index>=this.tabs.length||from===index)return false;
      const [tab]=this.tabs.splice(from,1);this.tabs.splice(index,0,tab);return true;
    }
    close(id){const index=this.tabs.findIndex(tab=>tab.id===id);if(index<0)return this.active;this.tabs.splice(index,1);if(id===this.activeId)this.activeId=this.tabs[Math.min(index,this.tabs.length-1)]?.id||'';if(!this.tabs.length)this.open('start');return this.active;}
    resetSession(){for(const tab of [...this.tabs])if(tab.kind==='file')this.close(tab.id);}
  }
  function recentList(items){return Array.isArray(items)?items.filter(item=>item&&typeof item.session==='string'&&typeof item.file?.id==='string'&&typeof item.file?.name==='string'&&Number.isFinite(item.at)).slice(0,20):[];}
  const sites=Object.freeze({chatgpt:'https://chatgpt.com/',images:'https://chatgpt.com/images',github:'https://github.com/pulls'});
  function normalizeSite(raw){
    if(typeof raw!=='string'||raw.length>4096)throw Error('Некорректный адрес.');
    const value=raw.trim();if(!value)throw Error('Введите адрес или запрос.');
    if(value==='chrome://webrtc-internals'||value==='chrome://webrtc-internals/')return 'chrome://webrtc-internals/';
    const url=new URL(value.includes('://')?value:/^[^\s]+\.[^\s]+$/.test(value)?'https://'+value:'https://www.google.com/search?q='+encodeURIComponent(value));
    if(url.protocol!=='https:'||url.username||url.password)throw Error('Нужен HTTPS-адрес.');
    return url.href;
  }
  function isChatGPT(value){try{return new URL(value).origin==='https://chatgpt.com'}catch{return false}}
  function taskError(task,profile){
    if(task?.status!=='fail'||task.error_message!=='chatgpt_login_required')return '';
    return 'Исполнитель «'+profile+'» получил запрос, но его подключение OpenAI вернуло chatgpt_login_required. Проверь подключение этого профиля в настройках Игорька. Отдельный вход для чата PR не нужен; пароль менять не нужно.';
  }
  const api={Model,recentList,sites,normalizeSite,isChatGPT,taskError};if(typeof module!=='undefined'&&module.exports)module.exports=api;else global.IgorekWorkspaceTabs=api;
})(typeof window==='undefined'?globalThis:window);
