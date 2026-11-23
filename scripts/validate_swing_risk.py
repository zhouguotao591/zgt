"""Fixed-rule chronological regression on frozen six-strategy data.

This script never constructs Service and never opens the live database.
No parameter search or winner selection is performed on the final segment.
"""
import os,sys,json,hashlib,time
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from pc28.trend_risk import DEFAULT,NEW_DEFAULT,analyze,transition
from scripts.analyze_strategy_regimes import bars_for,metrics

OUT=ROOT/'data/audits/swing-risk-20261122'


def mask_for(frame,bars,rule):
 records=[{'time':at.isoformat(),**{k:int(row[k]) if k=='orders' else float(row[k]) for k in ('open','high','low','close','orders')}} for at,row in bars.iterrows()]
 allowed=[];state={};events=[];last=None
 for j,end in enumerate(bars.end):
  end=int(end);r=analyze(records[max(0,j-180):j+1],rule,end)
  state={**transition(state,r,rule,end),'closed_at':end,'analysis':r}
  ok=not state['blocked'] and (r['status']!='unknown' or rule['unknown_policy']=='allow');allowed.append(ok)
  if ok!=last:events.append({'time':datetime.fromtimestamp(end,timezone.utc).isoformat(),'action':'resume' if ok else 'pause','reason':r['reason']})
  last=ok
 # Strictly before the draw's decision: no target-period outcome can enter.
 index=np.searchsorted(bars.end.to_numpy(),frame.decision.to_numpy(),side='left')-1
 mask=np.array([allowed[i] if i>=0 else False for i in index],dtype=bool)
 return mask,events


def main():
 if os.name=='nt':
  import ctypes
  ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(),0x4000)
 OUT.mkdir(parents=True,exist_ok=True)
 source=ROOT/'data/audits/candle-reversals-20261121/frames.npz';z=np.load(source)
 prior=json.loads((ROOT/'data/audits/six-strategy-entries-20261121/study.json').read_text(encoding='utf-8'))
 n=len(z['draw']);parts={'first60':np.arange(n)<int(n*.6),'middle20':(np.arange(n)>=int(n*.6))&(np.arange(n)<int(n*.8)),'last20':np.arange(n)>=int(n*.8)}
 output={'cutoff':int(z['cutoff']),'draws':n,'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'start':datetime.fromtimestamp(float(z['decision'][0]),timezone.utc).isoformat(),'end':datetime.fromtimestamp(float(z['decision'][-1]),timezone.utc).isoformat(),
         'scope':'固定100U模拟信号收益；历史数据此前已分析过，后段校验并非全新未知样本；未调参、未按后段结果挑选模型；不代表实际机器人收益。','results':[]}
 for i,identity in enumerate(prior['results']):
  f=pd.DataFrame({'draw':z['draw'],'decision':z['decision'],'settled':z['settled'],'pnl':z['pnl'][i],'hit':z['hit'][i],'stake':100.})
  f['day']=np.floor(f.decision/86400).astype(int)
  item={'id':identity['id'],'name':identity['name'],'baseline':{key:metrics(f,p) for key,p in parts.items()},'models':{}}
  for hours in (1,4):
   bars=bars_for(f,hours)
   for model in ('hybrid','swing'):
    rule={**(NEW_DEFAULT if model=='swing' else DEFAULT),'decision_model':model,'mode':'auto','interval':str(hours)+'h'}
    mask,events=mask_for(f,bars,rule)
    # Prefix invariant: later history must not alter any prior admission.
    cut=int(n*.8);prefix=f.iloc[:cut];available=bars.loc[bars.index<pd.Timestamp(float(prefix.decision.max()),unit='s',tz='UTC')]
    short,_=mask_for(prefix,available,rule)
    assert np.array_equal(short,mask[:cut]),(identity['id'],hours,model,'future leakage')
    item['models'][str(hours)+'h_'+model]={'settings':rule,'periods':{key:metrics(f,mask&p) for key,p in parts.items()},'actions':len(events),'events':events,'future_invariance':True}
    if model=='swing':
     checks={}
     for part in ('middle20','last20'):
      m=item['models'][str(hours)+'h_'+model]['periods'][part];base=item['baseline'][part]
      checks[part]=bool(m['bets']>=200 and m['net']>0 and m['roi'] is not None and m['roi']>base['roi'] and m['prediction_hit']>base['prediction_hit'] and m['max_dd']<=base['max_dd'])
     item['models'][str(hours)+'h_'+model]['fixed_validation']={'criteria':'每段至少200单、净收益正、投入收益率及命中率优于不筛选、最大回撤不高于不筛选；中20%与后20%均通过','periods':checks,'passed':all(checks.values())}
    print(json.dumps({'strategy':identity['id'],'hours':hours,'model':model,'last20':item['models'][str(hours)+'h_'+model]['periods']['last20']},ensure_ascii=True),flush=True)
  output['results'].append(item)
  (OUT/'validation.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
 passed=sum(m['fixed_validation']['passed'] for item in output['results'] for m in item['models'].values() if 'fixed_validation' in m)
 output['validation_summary']={'swing_configs':12,'passed_both_segments':passed,'automatic_enabling':False,'note':'这些历史此前已分析，不属于全新未知样本；没有按后段结果调参选优。'}
 (OUT/'validation.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
 lines=['波段反转模型固定规则验证（2026-11-22）',output['scope'],f"冻结数据：{output['start']} 至 {output['end']}，{n}期，截止{output['cutoff']}期。",'按时间前60%/中20%/后20%分段；下表为后20%，初始与结束均允许空仓。','每个模型保留其既定默认参数；表格不是调参挑选结果。','策略 / 模型 / 下单数 / 净收益U / 投入收益率 / 预测命中率 / 最大回撤U']
 for item in output['results']:
  for name,metric in [('不筛选',item['baseline']['last20'])]+[(k,v['periods']['last20']) for k,v in item['models'].items()]:
   rate=lambda v:'—' if v is None else f'{v*100:.2f}%'
   lines.append(f"{item['name']} / {name} / {metric['bets']} / {metric['net']:.0f} / {rate(metric['roi'])} / {rate(metric['prediction_hit'])} / {metric['max_dd']:.0f}")
 lines+=['','形态信号只在确认收盘后生效，不能按事后最高/最低点标记成交。','固定100U分析不覆盖会员实际金额、网络延迟或资金风控，正式使用前应在当前订阅金额方案下对比回测。','减少最大回撤不等于提高净收益；少下单可能降低回撤，也可能错过盈利波段。']
 lines+=['',f'12个波段配置中，中段与后段同时满足固定检查的为{passed}个；未自动启用任何订阅。','固定检查：每段至少200单，净收益正，投入收益率与命中率高于不筛选，回撤不高于不筛选。','部分配置仍亏损或逊于不筛选，不能统一应用到所有策略，不能视为已经证明持续提高盈利。']
 (OUT/'验证结果.txt').write_text('\n'.join(lines),encoding='utf-8')

if __name__=='__main__':main()
