(function(root,factory){const api=factory();if(typeof module==='object'&&module.exports)module.exports=api;else root.IgorekNavigationHistory=api;})(typeof globalThis==='object'?globalThis:this,function(){
  function route(value){
    if(!value||!['main','scheduled','reviews','plugins'].includes(value.view))return null;
    const session=String(value.session||'');if(session&&!/^[A-Za-z0-9_-]{1,256}$/.test(session))return null;
    const tab=typeof value.tab==='string'&&value.tab.length<=200?value.tab:'';
    const pull=typeof value.pull==='string'&&/^https:\/\/github\.com\/[^/]+\/[^/]+\/pull\/\d+\/?$/.test(value.pull)?value.pull:'';
    return {view:value.view,session,panel:value.panel===true,tab,pull};
  }
  class History{
    constructor({restore,changed=()=>{},limit=100}){this.restore=restore;this.changed=changed;this.limit=limit;this.entries=[];this.index=-1;this.busy=false;this.epoch=0;}
    state(){return {canBack:!this.busy&&this.index>0,canForward:!this.busy&&this.index>=0&&this.index<this.entries.length-1,busy:this.busy};}
    notify(){this.changed(this.state());}
    record(value){
      const item=route(value);if(!item||this.busy)return false;
      if(JSON.stringify(item)===JSON.stringify(this.entries[this.index]))return false;
      this.entries=this.entries.slice(0,this.index+1);this.entries.push(item);
      if(this.entries.length>this.limit)this.entries.shift();this.index=this.entries.length-1;this.notify();return true;
    }
    async move(delta){
      const target=this.index+delta;if(this.busy||target<0||target>=this.entries.length)return false;
      const epoch=++this.epoch;this.busy=true;this.notify();
      try{
        const actual=await this.restore({...this.entries[target]});
        if(epoch!==this.epoch)return false;
        this.entries[target]=route(actual)||this.entries[target];this.index=target;return true;
      }finally{if(epoch===this.epoch){this.busy=false;this.notify();}}
    }
    back(){return this.move(-1);}
    forward(){return this.move(1);}
    reset(){this.epoch++;this.entries=[];this.index=-1;this.busy=false;this.notify();}
  }
  return {History};
});
