(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.IgorekCharts = api;
})(typeof globalThis === 'object' ? globalThis : this, function() {
  'use strict';
  const ui = (ru,en) => globalThis.IgorekI18n?.lang === 'en' ? en : ru;
  const colors = ['#c4f26c', '#66d6b1', '#9fc9f3', '#d9b0f3', '#f4a96c', '#f17f74'];
  let menuSequence = 0;
  const isTool = name => /(?:^|__|\.)render_chart$/.test(String(name || ''));
  function validate(raw) {
    if (!raw || typeof raw !== 'object' || JSON.stringify(raw).length > 24000) throw Error('Некорректные данные графика');
    const text = (v, n, required) => { if (typeof v !== 'string' || [...v].length > n || (required && !v.trim()) || /[\x00-\x1f]/.test(v)) throw Error('Неверная подпись графика'); return v.trim(); };
    if (!['bar','line','pie'].includes(raw.type)) throw Error('Неизвестный тип графика');
    const title = text(raw.title, 160, true);
    if (!Array.isArray(raw.labels) || !raw.labels.length || raw.labels.length > 40) throw Error('Нужно от 1 до 40 подписей');
    const labels = raw.labels.map(v => text(v, 100, true));
    if (new Set(labels).size !== labels.length) throw Error('Подписи должны различаться');
    if (!Array.isArray(raw.series) || !raw.series.length || raw.series.length > 6) throw Error('Нужно от 1 до 6 рядов');
    const series = raw.series.map(s => { if (!s || !Array.isArray(s.values) || s.values.length !== labels.length || s.values.some(v => typeof v !== 'number' || !Number.isFinite(v) || Math.abs(v) > 1e12)) throw Error('Для каждой подписи нужно конечное числовое значение'); return {name:text(s.name,100,true),values:s.values.slice()}; });
    if (new Set(series.map(s=>s.name)).size !== series.length) throw Error('Названия рядов должны различаться');
    if (raw.type === 'pie' && (series.length !== 1 || series[0].values.some(v=>v<0) || !series[0].values.some(v=>v>0))) throw Error('Круговой диаграмме нужен один положительный ряд');
    const chart = {type:raw.type,title,labels,series,unit:text(raw.unit || '',40,false),source:text(raw.source || '',500,false)};
    for (const k of ['y_min','y_max']) if (raw[k] !== undefined) { if (typeof raw[k] !== 'number' || !Number.isFinite(raw[k]) || Math.abs(raw[k])>1e12) throw Error('Неверная шкала'); chart[k]=raw[k]; }
    const values=series.flatMap(s=>s.values), low=chart.y_min ?? Math.min(0,...values), high=chart.y_max ?? Math.max(0,...values);
    if (low>Math.min(...values) || high<Math.max(...values) || (chart.y_min!==undefined && chart.y_max!==undefined && low>=high)) throw Error('Шкала должна включать все значения');
    if (raw.type==='bar' && (low>0 || high<0)) throw Error('Шкала столбцов должна включать ноль');
    return chart;
  }
  function decode(item) {
    if (!isTool(item.name)) return null;
    try {
      const result=typeof item.result==='string'?JSON.parse(item.result):item.result;
      if (!result || result.ok!==true || result.kind!=='igorek.chart' || result.schema_version!==1 || !/^[A-Za-z0-9_-]{1,64}$/.test(result.chart_id)) return null;
      const input=typeof item.input==='string'?JSON.parse(item.input):item.input;
      return {chart:validate(input),chart_id:result.chart_id,revision:result.revision};
    } catch (_) { return null; }
  }
  function project(items) {
    const out=[], positions=new Map();
    for (const item of items) {
      const decoded=decode(item);
      if (!decoded) { out.push(item); continue; }
      const owner=decoded.chart_id;
      if (positions.has(owner)) {
        const index=positions.get(owner), previous=out[index];
        out[index]={...item,id:previous.id,chart_revisions:[...(previous.chart_revisions || [previous]),item]};
      } else { positions.set(owner,out.length); out.push(item); }
    }
    return out;
  }
  const number=(v,precision=17)=>new Intl.NumberFormat(globalThis.IgorekI18n?.lang==='en'?'en-US':'ru-RU',{maximumSignificantDigits:precision,notation:v!==0 && Math.abs(v)<1e-6?'scientific':'standard'}).format(v);
  const escape=v=>String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]));
  function range(chart) {
    const values=chart.series.flatMap(s=>s.values);let lo=chart.y_min ?? Math.min(0,...values),hi=chart.y_max ?? Math.max(0,...values);
    if (lo===hi) { lo-=1;hi+=1; } return [lo,hi];
  }
  function csv(chart) {
    const cell=v=>'"'+String(typeof v==='string' && /^[=+\-@]/.test(v) ? "'"+v : v).replace(/"/g,'""')+'"';
    return [[ui('Категория','Category'),...chart.series.map(s=>s.name+(chart.unit?' ('+chart.unit+')':''))],...chart.labels.map((label,i)=>[label,...chart.series.map(s=>s.values[i])])].map(row=>row.map(cell).join(',')).join('\r\n');
  }
  function svg(chart, width=720) {
    width=Math.max(300,width); const height=320, left=64,right=20,top=24,bottom=70,w=width-left-right,h=height-top-bottom;
    const [lo,hi]=range(chart), y=v=>top+(hi-v)/(hi-lo)*h, parts=[];
    const label=(x,y,value,anchor='middle',extra='')=>parts.push(`<text x="${x}" y="${y}" text-anchor="${anchor}" fill="#b8c0bd" font-size="12" ${extra}>${escape(value)}</text>`);
    if (chart.type==='pie') {
      const values=chart.series[0].values,total=values.reduce((a,b)=>a+b,0),cx=width/2,cy=144,r=102;let a=-Math.PI/2;
      values.forEach((v,i)=>{ const b=a+v/total*2*Math.PI;
        if (v===total) parts.push(`<circle cx="${cx}" cy="${cy}" r="${r}" fill="${colors[i%6]}"><title>${escape(chart.labels[i]+': '+number(v))}</title></circle>`);
        else if(v>0) parts.push(`<path d="M${cx},${cy} L${cx+r*Math.cos(a)},${cy+r*Math.sin(a)} A${r},${r} 0 ${b-a>Math.PI?1:0} 1 ${cx+r*Math.cos(b)},${cy+r*Math.sin(b)} Z" fill="${colors[i%6]}"><title>${escape(chart.labels[i]+': '+number(v))}</title></path>`); a=b; });
    } else {
      for(let i=0;i<=4;i++){const v=lo+(hi-lo)*i/4,yy=y(v);parts.push(`<path d="M${left},${yy}H${width-right}" stroke="#343c3e" fill="none"/>`);label(left-10,yy+4,number(v,5),'end');}
      label(left,14,chart.unit || ui('Значение','Value'),'start');
      const step=w/chart.labels.length;
      chart.series.forEach((series,s)=>{
        if(chart.type==='line') parts.push(`<polyline points="${series.values.map((v,i)=>`${left+step*(i+.5)},${y(v)}`).join(' ')}" fill="none" stroke="${colors[s%6]}" stroke-width="2.5"/>`);
        series.values.forEach((v,i)=>{const x=left+step*(i+.5),tooltip=escape(chart.labels[i]+' · '+series.name+': '+number(v)+' '+chart.unit);
          if(chart.type==='bar'){const bw=Math.min(48,step*.78/chart.series.length),xx=x+(s-chart.series.length/2)*bw;parts.push(`<rect x="${xx}" y="${Math.min(y(0),y(v))}" width="${Math.max(1,bw-3)}" height="${Math.max(1,Math.abs(y(v)-y(0)))}" rx="3" fill="${colors[s%6]}"><title>${tooltip}</title></rect>`);}
          else parts.push(`<circle cx="${x}" cy="${y(v)}" r="4" fill="${colors[s%6]}"><title>${tooltip}</title></circle>`);
        });
      });
      const count=width<500?4:8, every=Math.max(1,Math.ceil(chart.labels.length/count));
      chart.labels.forEach((v,i)=>{if(i%every!==0 && i!==chart.labels.length-1)return; const budget=Math.max(8,Math.min(40,Math.floor((step*every-8)/7))),short=v.length>budget?v.slice(0,budget-1)+'…':v;label(left+step*(i+.5),height-bottom+24,short);});
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${escape(chart.title)}" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}"><title>${escape(chart.title)}</title><rect width="100%" height="100%" fill="#171a1d"/>${parts.join('')}</svg>`;
  }
  function exportSvg(chart) {
    const width=960, split=text=>Array.from(text).reduce((lines,c)=>{if(!lines.length || lines[lines.length-1].length>=105)lines.push('');lines[lines.length-1]+=c;return lines;},[]);
    const headings=split(chart.title), legends=chart.type==='pie'?chart.labels.map((label,i)=>label+': '+number(chart.series[0].values[i])+(chart.unit?' '+chart.unit:'')):chart.series.map(s=>s.name);
    const source=chart.source?split(ui('Источник: ','Source: ')+chart.source):[], top=24+headings.length*22, height=top+320+legends.length*22+source.length*18+28;
    const text=(x,y,value,size=12)=>`<text x="${x}" y="${y}" fill="#edf0eb" font-size="${size}">${escape(value)}</text>`;
    const parts=[`<rect width="100%" height="100%" fill="#171a1d"/>`];
    headings.forEach((line,i)=>parts.push(text(24,28+i*22,line,16)));
    parts.push(svg(chart,width).replace('<svg ',`<svg x="0" y="${top}" `));
    legends.forEach((line,i)=>{const y=top+338+i*22;parts.push(`<circle cx="28" cy="${y-4}" r="4" fill="${colors[i%6]}"/>`);parts.push(text(40,y,line));});
    source.forEach((line,i)=>parts.push(text(24,top+350+legends.length*22+i*18,line)));
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><title>${escape(chart.title)}</title>${parts.join('')}</svg>`;
  }
  function render(item) {
    const card=document.createElement('article');card.className='chart-card';let current=item, chart=null, observer=null, dataOpen=false, versionMenu=null;
    const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
    const closeVersions=()=>{if(versionMenu?.matches(':popover-open'))versionMenu.hidePopover();};
    const draw=()=>{
      closeVersions();versionMenu=null;
      const previousData=card.querySelector('details.chart-data');if(previousData)dataOpen=previousData.open;
      card.replaceChildren();const decoded=decode(current);
      if(!decoded){const running=['running','pending','queued'].includes(current.status);card.append(el('div','chart-title',running?ui('Строю график…','Building chart…'):ui('Не удалось построить график','Could not build chart')));if(!running){let err='Проверьте подписи, количество значений и шкалу.';try{err=JSON.parse(current.result).error || err;}catch(_){}card.append(el('p','chart-caption',String(err)));}return;}
      chart=decoded.chart;card.append(el('h3','chart-title',chart.title));
      const plot=el('div','chart-desktop');
      const repaint=()=>{const template=document.createElement('template');template.innerHTML=svg(chart,Math.max(300,Math.floor(card.clientWidth-40)));plot.replaceChildren(template.content.cloneNode(true));};
      // SVG markup is produced exclusively from validated numbers and escaped text.
      card.append(plot);repaint();if(observer)observer.disconnect();observer=new ResizeObserver(()=>{if(!card.isConnected){observer.disconnect();return;}repaint();});observer.observe(card);
      const mobile=el('div','chart-mobile'), [lo,hi]=range(chart);
      if(chart.type==='line' || chart.type==='pie'){const picture=el('div','chart-mini-plot');const template=document.createElement('template');template.innerHTML=svg(chart,Math.max(300,Math.floor(card.clientWidth-40)));picture.append(template.content.cloneNode(true));mobile.append(picture);}
      chart.labels.forEach((name,i)=>{const group=el('div','chart-category');group.append(el('div','chart-category-name',name));chart.series.forEach((s,j)=>{const row=el('div','chart-value-row');row.append(el('span','',s.name),el('span','chart-value',number(s.values[i])+(chart.unit?' '+chart.unit:'')));group.append(row);if(chart.type==='bar'){const track=el('div','chart-track'),bar=el('span','chart-bar'),base=(0-lo)/(hi-lo)*100,end=(s.values[i]-lo)/(hi-lo)*100;bar.style.left=Math.min(base,end)+'%';bar.style.width=Math.abs(end-base)+'%';bar.style.background=colors[j%6];track.append(bar);group.append(track);}});mobile.append(group);});card.append(mobile);
      const legend=el('div','chart-legend');(chart.type==='pie'?chart.labels:chart.series.map(s=>s.name)).forEach((name,i)=>{const entry=el('span','chart-legend-entry'),swatch=el('i');swatch.style.background=colors[i%6];entry.append(swatch,document.createTextNode(name));legend.append(entry);});card.append(legend);
      if(chart.source)card.append(el('p','chart-caption',ui('Источник: ','Source: ')+chart.source));
      const actions=el('div','chart-actions'), data=el('details','chart-data'),summary=el('summary','', ui('Данные','Data'));data.open=dataOpen;data.append(summary);
      const table=el('table'),thead=el('thead'),head=el('tr');[ui('Категория','Category'),...chart.series.map(s=>s.name)].forEach(n=>head.append(el('th','',n)));thead.append(head);table.append(thead);const tbody=el('tbody');chart.labels.forEach((label,i)=>{const row=el('tr');[label,...chart.series.map(s=>number(s.values[i]))].forEach(v=>row.append(el('td','',v)));tbody.append(row);});table.append(tbody);data.append(table);card.append(data);
      const download=(label,mime,content,extension)=>{const b=el('button','',label);b.type='button';b.onclick=()=>{const url=URL.createObjectURL(new Blob([content()],{type:mime})),a=el('a');a.href=url;a.download='chart-'+decoded.chart_id+'.'+extension;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};return b;};
      actions.append(download(ui('Скачать SVG','Download SVG'),'image/svg+xml',()=>exportSvg(chart),'svg'),download('CSV','text/csv;charset=utf-8',()=> '\ufeff'+csv(chart),'csv'));
      const expand=el('button','',ui('Развернуть','Expand'));expand.dataset.chartExpand='true';expand.type='button';expand.onclick=()=>{const dialog=el('dialog','chart-dialog'),copy=render(current),close=el('button','',ui('Закрыть','Close'));close.type='button';close.onclick=()=>{copy.disposeChart?.();dialog.close();dialog.remove();};dialog.append(close,copy);dialog.addEventListener('cancel',()=>{copy.disposeChart?.();dialog.remove();});document.body.append(dialog);dialog.showModal();};actions.append(expand);
      if(current.chart_revisions?.length>1){
        const versions=current.chart_revisions,selected=current.chart_selected_version ?? versions.length-1;
        const trigger=el('button','chart-version-trigger',ui('Версия ','Version ')+(selected+1));trigger.type='button';
        const menu=el('div','chart-version-menu');menu.id='chart-versions-'+(++menuSequence);menu.popover='auto';menu.setAttribute('role','menu');menu.setAttribute('aria-label',ui('Версии графика','Chart versions'));
        trigger.setAttribute('aria-haspopup','menu');trigger.setAttribute('aria-controls',menu.id);trigger.setAttribute('aria-expanded','false');
        const options=versions.map((revision,index)=>{
          const option=el('button','chart-version-option',ui('Версия ','Version ')+(index+1));option.type='button';option.setAttribute('role','menuitemradio');option.setAttribute('aria-checked',String(index===selected));option.tabIndex=index===selected?0:-1;
          option.onclick=()=>{menu.hidePopover();current={...versions[index],id:current.id,chart_revisions:versions,chart_selected_version:index};draw();card.querySelector('.chart-version-trigger')?.focus();};menu.append(option);return option;
        });
        const openVersions=()=>{
          if(!menu.matches(':popover-open'))menu.showPopover();
          const rect=trigger.getBoundingClientRect(),height=menu.offsetHeight,width=menu.offsetWidth;menu.style.left=Math.max(8,Math.min(rect.left,window.innerWidth-width-8))+'px';menu.style.top=Math.max(8,rect.bottom+6+height<=window.innerHeight-8?rect.bottom+6:rect.top-height-6)+'px';options[selected].focus();
        };
        trigger.onclick=()=>{if(menu.matches(':popover-open'))menu.hidePopover();else openVersions();};
        menu.addEventListener('toggle',event=>trigger.setAttribute('aria-expanded',String(event.newState==='open')));
        menu.addEventListener('keydown',event=>{
          if(event.key==='Escape'){event.preventDefault();event.stopPropagation();menu.hidePopover();trigger.focus();return;}
          if(event.key==='Tab'){menu.hidePopover();trigger.focus();return;}
          const index=options.indexOf(document.activeElement);let next;
          if(event.key==='ArrowDown')next=(index+1)%options.length;
          else if(event.key==='ArrowUp')next=(index-1+options.length)%options.length;
          else if(event.key==='Home')next=0;else if(event.key==='End')next=options.length-1;
          if(next!==undefined){event.preventDefault();options[next].focus();}
        });
        trigger.addEventListener('keydown',event=>{if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();openVersions();}});
        versionMenu=menu;actions.append(trigger,menu);
      }
      card.append(actions);
    };
    card.updateChart=next=>{current=next;draw();};card.disposeChart=()=>{closeVersions();observer?.disconnect();};draw();return card;
  }
  return {validate,decode,project,isTool,svg,exportSvg,csv,range,render,colors};
});
