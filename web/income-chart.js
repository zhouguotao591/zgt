'use strict';
const incomeOverlays={trend:true,levels:true,boll:true};
const pendingIncomeCharts=new Map();
let incomeChartSequence=0;

function visibleIncomeLevels(bars,first,last){
 const window=bars.slice(first,last+1);
 const highs=[],lows=[];
 for(let i=first+2;i<=last-2;i++){
  const neighbors=[bars[i-2],bars[i-1],bars[i+1],bars[i+2]];
  if(neighbors.every(b=>bars[i].high>b.high))highs.push(bars[i].high);
  if(neighbors.every(b=>bars[i].low<b.low))lows.push(bars[i].low);
 }
 return [{label:'支撑',value:lows.at(-1)??Math.min(...window.map(b=>b.low)),color:'#168877'},
         {label:'压力',value:highs.at(-1)??Math.max(...window.map(b=>b.high)),color:'#d97706'}];
}

const incomeChartResize=new ResizeObserver(entries=>{
 for(const entry of entries)entry.target.dispatchEvent(new Event('income-resize'));
});
new MutationObserver(()=>{
 for(const [id,initialize] of pendingIncomeCharts){
  const root=document.querySelector('[data-income-chart="'+id+'"]');
  if(root){
   pendingIncomeCharts.delete(id);
   initialize(root);
  }
 }
}).observe(document.documentElement,{childList:true,subtree:true});

function incomeIndicators(bars){
 const boll=[];
 for(let i=19;i<bars.length;i++){
  const closes=bars.slice(i-19,i+1).map(b=>b.close);
  const middle=closes.reduce((sum,v)=>sum+v,0)/20;
  const deviation=Math.sqrt(closes.reduce((sum,v)=>sum+(v-middle)**2,0)/20);
  boll.push({index:i,middle,upper:middle+2*deviation,lower:middle-2*deviation});
 }
 const start=Math.max(0,bars.length-60),highs=[],lows=[];
 for(let i=start+2;i<bars.length-2;i++){
  const neighbors=[bars[i-2],bars[i-1],bars[i+1],bars[i+2]];
  if(neighbors.every(b=>bars[i].high>b.high))highs.push({index:i,value:bars[i].high});
  if(neighbors.every(b=>bars[i].low<b.low))lows.push({index:i,value:bars[i].low});
 }
 const trend=[highs,lows].filter(points=>points.length>=2).map(points=>{
  const [a,b]=points.slice(-2);
  return [a,b];
 });
 const levels=bars.length?[{label:'支撑',value:lows.at(-1)?.value??Math.min(...bars.slice(start).map(b=>b.low))},
                          {label:'压力',value:highs.at(-1)?.value??Math.max(...bars.slice(start).map(b=>b.high))}]:[];
 return {boll,trend,levels,start};
}

document.addEventListener('change',event=>{
 const input=event.target.closest('[data-income-overlay]');
 if(!input)return;
 const root=input.closest('.income-chart');
 if(!root)return;
 const key=input.dataset.incomeOverlay;
 incomeOverlays[key]=input.checked;
 for(const group of root.querySelectorAll('[data-income-layer="'+key+'"]'))group.style.display=input.checked?(group.tagName.toLowerCase()==='div'?'flex':''):'none';
});

window.pc28IncomeChart=function(bars,candleInterval,emptyMessage,allowOrderGrouping=false,allowPeriodGrouping=false,options={}){
const id=String(++incomeChartSequence);
return '<div class="income-chart" data-income-chart="'+id+'">'+renderIncomeChart(bars,candleInterval,emptyMessage,allowOrderGrouping,allowPeriodGrouping,id,options)+'</div>';
};

function renderIncomeChart(bars,candleInterval,emptyMessage,allowOrderGrouping,allowPeriodGrouping,chartId,options){
const timeNames={'1h':'1小时','4h':'4小时','1d':'日线'};
const orderNames={'1o':'单笔订单','20o':'20笔订单','50o':'50笔订单','100o':'100笔订单'};
const periodNames={'1p':'单期','5p':'5期','10p':'10期','20p':'20期','50p':'50期','100p':'100期','500p':'500期'};const byPeriod=candleInterval in periodNames;const names={...timeNames,...orderNames,...periodNames};
const byOrder=candleInterval in orderNames;
const buttons=options=>Object.entries(options).map(([k,v])=>`<button type="button" data-k-interval="${k}" class="${k===candleInterval?'selected':'secondary'}" aria-pressed="${k===candleInterval}">${v}</button>`).join('');
const orderControls=allowOrderGrouping?`<div class="hb-k-periods" role="group" aria-label="按订单聚合">${buttons(orderNames)}</div>`:'';
const periodControls=allowPeriodGrouping?`<div class="hb-k-periods" role="group" aria-label="按开奖期数聚合">${buttons(periodNames)}</div>`:'';
const note=byPeriod?'从所选范围首期起按开奖期号分组 · 未下单期收益持平 · 末组不足也显示':byOrder?'按结算顺序连续分组 · 末组不足指定笔数也显示':'北京时间聚合 · 无成交时段收益持平';
const controls=Object.entries({trend:'趋势线',levels:'支撑 / 压力',boll:'BOLL（20，2）'}).map(([key,label])=>`<label style="display:inline-flex;align-items:center;gap:5px;margin-right:14px"><input type="checkbox" data-income-overlay="${key}" ${incomeOverlays[key]?'checked':''} style="width:auto">${label}</label>`).join('');
const header=`<div class="hb-k-head"><h3>累计净收益K线（U）</h3><div class="hb-k-periods" role="group" aria-label="按时间聚合">${buttons(timeNames)}</div>${orderControls}${periodControls}</div><div role="group" aria-label="K线辅助线">${controls}</div><p class="muted">${note} · 红涨绿跌 · 横向滚动查看历史 · 按钮或Ctrl＋滚轮缩放 · 鼠标悬停查看开高低收</p><p class="muted">辅助线按收益值计算。趋势连接近60根内最近两个确认高点或低点；支撑压力随左右滚动，按当前可见区间的最近确认低高点更新，无拐点时取可见区间极值；数值显示在图表下方。BOLL使用20根收盘值及2倍总体标准差，少于20根时暂不显示。</p>`;
const orderLabel=b=>b.first_order===b.last_order?'第'+b.first_order+'笔':b.first_order+'–'+b.last_order+'笔';
const hoverTime=b=>byPeriod?`${b.first_draw}—${b.last_draw}期`:byOrder?orderLabel(b)+' · '+beijingTime(b.time)+(b.end_time!==b.time?' 至 '+beijingTime(b.end_time):''):beijingTime(b.time);
const axisLabel=b=>byPeriod?`${b.first_draw}期`:byOrder?orderLabel(b):beijingTime(b.time).slice(0,candleInterval==='1d'?10:16);
if(!bars.length)return header+'<div class="empty">'+esc(emptyMessage)+'</div>';
const indicators=incomeIndicators(bars);
let low=Math.min(0,...bars.map(b=>b.low),...indicators.boll.map(b=>b.lower)),high=Math.max(0,...bars.map(b=>b.high),...indicators.boll.map(b=>b.upper)),span=high-low||1;low-=span*.06;high+=span*.06;const W=Math.max(960,bars.length*11+110),L=80,R=W-100,T=20,B=310,x=i=>L+(i+.5)*(R-L)/bars.length,y=v=>B-(v-low)/(high-low)*(B-T),body=Math.max(2,Math.min(9,(R-L)/bars.length*.7));
const layer=(key,content)=>`<g data-income-layer="${key}" style="pointer-events:none;${incomeOverlays[key]?'':'display:none'}">${content}</g>`;
const linePath=(points,color,dashed=false)=>`<polyline points="${points.map(p=>x(p.index)+','+y(p.value)).join(' ')}" fill="none" stroke="${color}" stroke-width="1.6" ${dashed?'stroke-dasharray="5 4"':''}/>`;
const markers=(options.markers||[]).map(m=>{let index;if(m.draw!=null&&bars[0].first_draw!=null)index=bars.findIndex(b=>b.last_draw>=m.draw);else{const stamp=new Date(m.time).getTime();index=bars.findIndex((b,i)=>new Date(b.time).getTime()<=stamp&&(i===bars.length-1||new Date(bars[i+1].time).getTime()>stamp));}if(index<0)return '';const stop=m.action==='pause',color=stop?'#db455c':'#087f68',yy=stop?16:326;return `<g><title>${esc((stop?'停止':'进场')+' · '+(m.time||'')+' · '+(m.reason||''))}</title><line x1="${x(index)}" x2="${x(index)}" y1="28" y2="310" stroke="${color}" stroke-dasharray="2 5" opacity=".35"/><text x="${x(index)}" y="${yy}" text-anchor="middle" font-size="11" fill="${color}">${stop?'▼停':'▲进'}</text></g>`;}).join('');
const overlay=layer('boll',['upper','middle','lower'].map((key,i)=>linePath(indicators.boll.map(b=>({index:b.index,value:b[key]})),i===1?'#a97908':'#6478d8',i===1)).join(''))+
 layer('trend',indicators.trend.map(points=>linePath(points,'#ae4ac2')).join(''))+
 layer('levels','');
pendingIncomeCharts.set(chartId,root=>{
 const scroller=root.querySelector('.hb-k-scroll');
 const svgChart=scroller.querySelector('svg');let zoom=1;
 const tools=document.createElement('div');tools.className='hb-k-periods';tools.style.cssText='display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0';
 tools.innerHTML='<button type="button" data-zoom="out" aria-label="缩小K线">－ 缩小</button><button type="button" data-zoom="in" aria-label="放大K线">＋ 放大</button><button type="button" data-zoom="fit">显示全部</button><button type="button" data-zoom="reset">重置</button><label>缩放 <input type="range" min="0.1" max="8" step="0.1" value="1" aria-label="K线缩放比例" style="width:150px;display:inline-block"></label><span data-zoom-label>100%</span>';
 scroller.closest('.hb-k-frame').before(tools);
 function resizeZoom(next,anchor=.5){const old=svgChart.getBoundingClientRect().width,position=(scroller.scrollLeft+scroller.clientWidth*anchor)/old;const minimum=Math.min(.1,scroller.clientWidth/W);tools.querySelector('input').min=minimum;zoom=Math.max(minimum,Math.min(8,next));svgChart.setAttribute('width',Math.max(scroller.clientWidth,W*zoom));svgChart.setAttribute('preserveAspectRatio','none');scroller.scrollLeft=position*svgChart.getBoundingClientRect().width-scroller.clientWidth*anchor;tools.querySelector('input').value=zoom;tools.querySelector('[data-zoom-label]').textContent=Math.round(zoom*100)+'%';schedule();}
 tools.querySelector('input').oninput=e=>resizeZoom(Number(e.target.value));
 tools.querySelectorAll('button').forEach(b=>b.onclick=()=>resizeZoom(({in:zoom*1.4,out:zoom/1.4,fit:scroller.clientWidth/W,reset:1})[b.dataset.zoom]));
 scroller.addEventListener('wheel',e=>{if(!e.ctrlKey&&!e.metaKey)return;e.preventDefault();resizeZoom(zoom*(e.deltaY<0?1.15:1/1.15),(e.clientX-scroller.getBoundingClientRect().left)/scroller.clientWidth);},{passive:false});
 const group=root.querySelector('[data-income-layer="levels"]');
 const labels=document.createElement('div');
 labels.dataset.incomeLayer='levels';
 labels.style.cssText='display:flex;flex-wrap:wrap;gap:8px 20px;padding:8px 12px;background:#f5f8fc;border-radius:7px;font-size:12px;margin-top:8px';
 if(!incomeOverlays.levels)labels.style.display='none';
 root.querySelector('.hb-k-frame').after(labels);
 let frame=0;
 function update(){
  frame=0;
  if(!scroller.clientWidth)return;
  const svg=scroller.querySelector('svg');
  const scale=svg.getBoundingClientRect().width/W;
  if(!scale)return;
  const left=Math.max(L,scroller.scrollLeft/scale);
  const right=Math.min(R,(scroller.scrollLeft+scroller.clientWidth)/scale);
  const step=(R-L)/bars.length;
  const first=Math.max(0,Math.min(bars.length-1,Math.ceil((left-L)/step-.5)));
  const last=Math.max(first,Math.min(bars.length-1,Math.floor((right-L)/step-.5)));
  const levels=visibleIncomeLevels(bars,first,last);
  group.innerHTML=levels.map(level=>`<line x1="${left}" x2="${right}" y1="${y(level.value)}" y2="${y(level.value)}" stroke="${level.color}" stroke-width="1.4" stroke-dasharray="6 4"/>`).join('');
  labels.replaceChildren(...levels.map(level=>{
   const label=document.createElement('span');
   label.style.color=level.color;
   label.textContent=level.label+' '+num(level.value)+' U';
   return label;
  }));
  const range=document.createElement('span');
  range.textContent='可见区间 '+axisLabel(bars[first])+' — '+axisLabel(bars[last]);
  range.style.color='#61758c';
  labels.append(range);
 }
 function schedule(){if(!frame)frame=requestAnimationFrame(update);}
 scroller.addEventListener('scroll',schedule,{passive:true});
 scroller.addEventListener('income-resize',schedule);
 incomeChartResize.observe(scroller);
 // Release the resize observation when a chart is replaced or its dialog closes.
 const cleanup=new MutationObserver(()=>{
  if(!root.isConnected){incomeChartResize.unobserve(scroller);cleanup.disconnect();if(frame)cancelAnimationFrame(frame);}
 });
 cleanup.observe(document.documentElement,{childList:true,subtree:true});
 update();
});
return header+`<div class="hb-k-frame"><svg class="hb-k-axis" width="90" height="320" aria-hidden="true">${Array.from({length:5},(_,i)=>{const v=low+(high-low)*i/4;return `<text x="10" y="${y(v)+4}" fill="#71839a" font-size="11">${num(v)}</text>`;}).join('')}</svg><div class="hb-k-scroll"><svg width="${W}" height="360" viewBox="0 0 ${W} 360" role="img" aria-label="${names[candleInterval]}累计净收益K线">${Array.from({length:5},(_,i)=>{const v=low+(high-low)*i/4;return `<line x1="${L}" x2="${R}" y1="${y(v)}" y2="${y(v)}" stroke="#e4ebf4"/><text x="6" y="${y(v)+4}" fill="#71839a" font-size="11">${num(v)}</text>`;}).join('')}<line x1="${L}" x2="${R}" y1="${y(0)}" y2="${y(0)}" stroke="#8295ab" stroke-dasharray="4 4"/>${bars.map((b,i)=>{const color=b.close>b.open?'#df5263':b.close<b.open?'#159478':'#8b9aae',xx=x(i),top=Math.min(y(b.open),y(b.close)),h=Math.max(1,Math.abs(y(b.open)-y(b.close)));return `<g><title>${esc(hoverTime(b))} · 开 ${num(b.open)} · 高 ${num(b.high)} · 低 ${num(b.low)} · 收 ${num(b.close)} · ${b.orders}笔</title><line x1="${xx}" x2="${xx}" y1="${y(b.high)}" y2="${y(b.low)}" stroke="${color}" stroke-width="1.4"/><rect x="${xx-body/2}" y="${top}" width="${body}" height="${h}" fill="${color}"/></g>`;}).join('')}${overlay}${markers}${bars.map((b,i)=>({b,i})).filter(({i})=>i%Math.max(1,Math.ceil(bars.length/Math.max(3,Math.floor(W/140))))===0).map(({b,i})=>`<text x="${x(i)}" y="340" text-anchor="middle" fill="#71839a" font-size="10">${esc(axisLabel(b))}</text>`).join('')}</svg></div></div><p class="muted">${bars.length}根K线 · 最后收盘累计收益 ${cash(bars.at(-1).close)}U；K线使用全部已结算订单逐笔聚合，不使用抽样曲线。</p>`;};
