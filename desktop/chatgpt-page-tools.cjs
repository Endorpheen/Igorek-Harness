const PAGE_TOOLS=`(()=>{
  if(location.origin!=='https://chatgpt.com'||window.__igorekPageTools)return;
  let overlay=null,target=null,anchor=null,timer=0,animations=[],updateMotion=null;
  const motion=window.matchMedia?.('(prefers-reduced-motion: reduce)');
  const stop=()=>{clearInterval(timer);timer=0;animations.forEach(animation=>animation.cancel());animations=[];updateMotion=null;overlay?.remove();overlay=null;target=null;anchor=null;};
  motion?.addEventListener('change',()=>updateMotion?.());
  const contains=(r,x,y)=>r.width>=48&&r.height>=48&&r.width<=320&&r.height<=320&&r.width/r.height>=.65&&r.width/r.height<=1.5&&x>=r.left&&x<=r.right&&y>=r.top&&y<=r.bottom;
  const pointAllowed=(x,y)=>Number.isFinite(x)&&Number.isFinite(y)&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight;
  const visualAt=(x,y)=>{
    const media=[...document.querySelectorAll('canvas,video,img,svg')].filter(node=>node.id!=='igorek-voice-avatar-prototype'&&!node.closest('#igorek-voice-avatar-prototype')&&contains(node.getBoundingClientRect(),x,y));
    media.sort((a,b)=>{const ra=a.getBoundingClientRect(),rb=b.getBoundingClientRect();return ra.width*ra.height-rb.width*rb.height;});
    if(media.length)return media[0];
    let node=document.elementFromPoint(x,y);
    for(let depth=0;node&&depth<5;depth++,node=node.parentElement){
      if(node===document.body||node===document.documentElement)break;
      const r=node.getBoundingClientRect();
      if(r.width>=48&&r.height>=48&&r.width<=320&&r.height<=320&&r.width/r.height>=.65&&r.width/r.height<=1.5&&(node.matches('canvas,video,img,svg')||!node.textContent.trim())&&!node.matches('button,a,input,textarea,[role="button"],[contenteditable="true"]')&&!node.querySelector('button,a,input,textarea,[role="button"],[contenteditable="true"]'))return node;
    }
    return null;
  };
  const probe=(x,y)=>({enabled:!!overlay,available:!!visualAt(x,y)||pointAllowed(x,y),selected:!!window.getSelection()?.toString()});
  const toggle=(x,y,image,winkImage='')=>{
    if(overlay){stop();return {enabled:false};}
    target=visualAt(x,y);if(!target){if(!pointAllowed(x,y))return {enabled:false,unavailable:true};anchor={x:x/innerWidth,bottom:innerHeight-y,size:112};}
    overlay=document.createElement('div');overlay.id='igorek-voice-avatar-prototype';overlay.setAttribute('aria-hidden','true');
    Object.assign(overlay.style,{position:'fixed',zIndex:'2147483647',pointerEvents:'none',background:'#171717',borderRadius:'50%',overflow:'hidden',display:'grid',placeItems:'center'});
    const fallback=document.createElement('span');fallback.textContent='🐙';Object.assign(fallback.style,{position:'absolute',fontSize:'60px'});
    const frame=document.createElement('div');frame.dataset.igorekAvatarFrame='';Object.assign(frame.style,{width:'100%',aspectRatio:'3 / 2',position:'relative',pointerEvents:'none'});
    const picture=document.createElement('img');picture.alt='';Object.assign(picture.style,{width:'100%',height:'100%',objectFit:'contain',position:'absolute',inset:'0',pointerEvents:'none'});
    picture.onload=()=>{fallback.hidden=true;};picture.onerror=()=>{picture.hidden=true;fallback.hidden=false;};picture.src=image;
    const wink=document.createElement('img');wink.alt='';wink.dataset.igorekAvatarWink='';Object.assign(wink.style,{width:'100%',height:'100%',objectFit:'contain',position:'absolute',inset:'0',opacity:'0',clipPath:'ellipse(6.4% 10.3% at 43% 42.5%)',pointerEvents:'none'});
    let winkReady=false;wink.onload=()=>{winkReady=true;updateMotion?.();};wink.onerror=()=>{wink.hidden=true;};if(winkImage)wink.src=winkImage;
    frame.append(picture,wink);
    const glow=document.createElement('div');glow.dataset.igorekAvatarGlow='';Object.assign(glow.style,{position:'absolute',inset:'8%',borderRadius:'50%',background:'radial-gradient(circle,rgba(183,232,96,.24),rgba(116,171,78,.08) 48%,transparent 72%)',pointerEvents:'none'});overlay.append(glow,fallback,frame);document.body.append(overlay);
    const breathe=()=>{animations.forEach(animation=>animation.cancel());animations=[];if(motion?.matches||!overlay)return;
      for(const node of [frame,fallback])animations.push(node.animate([{transform:'translateY(1px) scale(.97) rotate(-1deg)'},{transform:'translateY(-2px) scale(1.025) rotate(1deg)'},{transform:'translateY(1px) scale(.97) rotate(-1deg)'}],{duration:4600,iterations:Infinity,easing:'ease-in-out'}));
      animations.push(glow.animate([{opacity:.35,transform:'scale(.9)'},{opacity:1,transform:'scale(1.12)'},{opacity:.35,transform:'scale(.9)'}],{duration:3800,iterations:Infinity,easing:'ease-in-out'}));
      if(winkReady)animations.push(wink.animate([{opacity:0,offset:0},{opacity:0,offset:.9},{opacity:1,offset:.911},{opacity:1,offset:.933},{opacity:0,offset:.944},{opacity:0,offset:1}],{duration:6200,iterations:Infinity,easing:'linear'}));
    };updateMotion=breathe;breathe();

    const position=()=>{if(target&&!target.isConnected){stop();return;}const r=target?target.getBoundingClientRect():{left:Math.max(0,Math.min(innerWidth-anchor.size,innerWidth*anchor.x-anchor.size/2)),top:Math.max(0,Math.min(innerHeight-anchor.size,innerHeight-anchor.bottom-anchor.size/2)),width:anchor.size,height:anchor.size};if(r.width<1||r.height<1){overlay.style.display='none';return;}if(r.width>320||r.height>320){stop();return;}Object.assign(overlay.style,{display:'grid',left:r.left+'px',top:r.top+'px',width:r.width+'px',height:r.height+'px'});fallback.style.fontSize=Math.min(r.width,r.height)*.7+'px';};
    position();if(overlay)timer=setInterval(position,250);return {enabled:!!overlay};
  };
  Object.defineProperty(window,'__igorekPageTools',{value:{probe,toggle,stop}});
  window.addEventListener('pagehide',stop,{once:true});
  window.addEventListener('contextmenu',event=>{
    if(!event.isTrusted)return;
    if(window.getSelection()?.toString()||overlay||visualAt(event.clientX,event.clientY))event.stopImmediatePropagation();
  },true);
})()`;
module.exports={PAGE_TOOLS};
