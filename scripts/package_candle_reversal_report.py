"""Local-only delivery of the reviewed reversal-pattern research."""
import json,csv,sys
from pathlib import Path
from datetime import datetime,timezone,timedelta
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.analyze_candle_reversals import frames
from scripts.analyze_strategy_regimes import bars_for
from scripts.reversal_pattern_rules import detect,policy
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/audits/candle-reversals-20261121';study=json.loads((OUT/'study.json').read_text(encoding='utf8'));risk=json.loads((OUT/'risk-sensitivity.json').read_text(encoding='utf8'))
FAMILY={'fractal3':'三根顶底分型','star':'无跳空黎明／黄昏之星','engulf':'阳包阴／阴包阳','union':'分型＋星线＋吞没组合'}
MODE={'raw':'确认即用','level':'位置过滤＋形态失效退出','confirm':'位置过滤＋后续突破确认'}
PATTERNS={'top3':'顶分型（三根）','bottom3':'底分型（三根）','top5':'顶分形（五根）','bottom5':'底分形（五根）','morning_nogap':'黎明之星（无跳空适配）','evening_nogap':'黄昏之星（无跳空适配）','morning_gap':'黎明之星（严格实体跳空）','evening_gap':'黄昏之星（严格实体跳空）','bull_engulf':'阳包阴','bear_engulf':'阴包阳'}
WATCH=[('fixed_big','1h.union.level'),('fixed_small','4h.union.level'),('fixed_even','4h.star.level')]
def name(key):
 if key=='always':return '原始对照，不新增形态门禁'
 h,f,m=key.split('.');return h.replace('h','小时')+'／'+FAMILY[f]+'／'+MODE[m]
def n(x):return '—' if x is None else f'{x:,.0f}'
def pct(x):return '—' if x is None else f'{x*100:.2f}%'
def bj(sec):return datetime.fromtimestamp(float(sec),timezone.utc).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
def table(headers,rows):return '| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+'\n'.join('| '+' | '.join(str(x).replace('|','／') for x in row)+' |' for row in rows)
def result(sid):return next(r for r in study['results'] if r['id']==sid)
watchrows=[]
for sid,key in WATCH:
 r=result(sid);m=r['models'][key];z=m['replication'];b=r['baseline'];watchrows.append([r['name'],name(key),n(m['train']['net']),n(m['validation']['net']),n(b['net'])+' → '+n(z['net']),pct(b['prediction_hit'])+' → '+pct(z['prediction_hit']),n(b['max_dd'])+' → '+n(z['max_dd'])])
blocks=[
 '# 常规反转形态与六类基础策略专项评估',
 '编制日期：2026-11-21。本地只读研究；未修改生产配置、机器人、会员金额或自动下单开关。数据冻结至3509217期，北京时间2026-11-21 16:40。',
 '## 执行摘要',
 '**可以将分型、黎明之星、黄昏之星、阳包阴、阴包阳作为候选进场／停止信号，但应结合位置和确认时间，不能单看形态名称。** 本次新增的具体形态检验，找到了比上一轮通用趋势门禁更值得研究的候选，尤其是固定买小的4小时组合底部＋位置，以及固定买双的4小时无跳空星线＋位置。',
 '这些是从已查看的历史中形成的观察候选，不是已经证实的最优参数。部分模型在末段同时改善本金收益与条件命中率；但288项近似联合检验校正后，没有“总净收益与条件命中率共同改善”获得稳健统计支持的模型，且这批数据此前已被分析过，必须冻结后再验证新数据。',
 '本次区分两种收益率：本金收益率＝净收益÷初始本金；投入收益率＝净收益÷累计下注额。筛选可能提高后者、减少前者。全量预测方向不变，筛选后命中率只是被跟随子集的历史条件命中率。',
 '## 三组观察候选：不再给所有策略套同一规则',
 table(['策略','后验研究候选','训练净收益U','验证净收益U','末段净收益U：原→筛选','末段命中率：原→筛选','末段回撤U：原→筛选'],watchrows),
 '每笔100U，末段约66个自然日，每个原始策略25,924笔预测。本表未叠加资金停单，不是会员真实成交收益。三组候选在训练、验证、末段均为正，但并非都由“只看训练段选第一名”的流程选出；它们是完整观察结果中的后验研究清单。多重检验校正后本金目标的q均为1，不能据此声称将来必然提高盈利。',
 '## 形态的确认时间比图上形态位置更重要',
 table(['形态','本轮量化定义','何时允许使用'],[
 ['三根顶／底分型','中间K线的高点和低点均严格高于／低于两侧；不做缠论包含关系合并','右侧那根K线收盘后；不能回填到中间那根作为已知信号'],
 ['五根分形','中心高点高于前后各两根，或中心低点低于前后各两根；单独作诊断','右侧第二根收盘后；不与三根分型混用'],
 ['黎明之星适配版','前有下降；长阴＋小实体＋收复首根实体至少一半的阳线','第三根收盘后；本轮明确不要求实体跳空'],
 ['黄昏之星适配版','前有上升；长阳＋小实体＋回落至首根实体一半以下的阴线','第三根收盘后'],
 ['阳包阴／阴包阳','前有相应趋势；后根实体覆盖前根实体并严格越过一端，不要求吞没全部影线','后根收盘后']]),
 '为什么要适配星线：累计收益K线的下一根开盘通常等于上一根收盘，没有股票式实体跳空。本轮6个策略、1小时和4小时共12条序列中，严格要求首两根实体跳空的黎明／黄昏之星均为0次。不能把无跳空版当作标准股票指标的等价统计。',
 '形态定义参考：[StockCharts看涨反转](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-bullish-reversal-patterns)、[看跌反转](https://chartschool.stockcharts.com/table-of-contents/chart-analysis/candlestick-charts/candlestick-bearish-reversal-patterns)、[MetaQuotes五根分形](https://www.mql5.com/en/articles/1435)。上面的阈值、三根分型及无跳空处理是本研究的明确约定，不是唯一公认定义；不能直接移用这些资料中的股票效果到本站。',
 '## 入场与停止：建议冻结后测试的具体规则',
 '### 固定买小：4小时底部形态组合＋位置',
 '收益源为独立100U信号账本，方向始终为“小”。每根4小时K线收盘后，识别底分型、无跳空黎明之星、阳包阴中的任意一种。再要求形态最低点接近此前20根支撑或BOLL下轨；本轮“接近”容许0.25个标准差距离。形态及位置均确认后，才允许下一条新信号。',
 '停止：出现该组合中的顶分型、无跳空黄昏之星或阴包阳，或者已收盘累计信号收益跌破入场形态低点。相互冲突时停止优先。这里是停止后续买小，不是改买大，也不是撤回已下注订单。',
 '### 固定买双：4小时星线＋位置',
 '方向始终为“双”。入场限于已收盘无跳空黎明之星，且形态低点接近此前20根支撑或BOLL下轨。停止限于已收盘无跳空黄昏之星或跌破入场形态低点。这组不强行把分型和吞没都加进去；本轮训练／验证／末段净收益分别为5,000／3,700／27,700U。',
 '### 固定买大：1小时底部形态组合＋位置',
 '规则与买小组合相同，周期改为1小时、方向固定“大”。训练／验证／末段净收益分别4,600／3,200／5,000U，末段由原始亏损转为盈利。但增益仍可能是偶然，不能仅凭这一段转正直接启用。',
 '这些形态不能用当前未收盘K线识别。BOLL为此前20根收盘值均值±2倍总体标准差；支撑压力是此前20根低点最小值／高点最大值。长实体以形态开始前20根实体中位数为参考，小实体不超过首根及参考实体的一半。',
 '## 更保守的突破确认：命中率提高不等于本金收益提高',
 '固定买双的4小时“分型／黎明之星／阳包阴组合＋位置＋后续突破确认”是另一条线索。底部形态确认后先不执行；接下来最多3根已收盘4小时K线中，收盘突破该形态覆盖区间高点才允许新预测；跌破低点或超时就作废。入场后遇反向组合形态或跌破原低点即停止。',
 '末段：只放行3,360／25,924笔（约12.96%），方向命中率50.51%→52.32%，投入收益率0.91%→4.55%，回撤19,100→2,800U；但净收益23,500→15,300U。同本金且不设资金停单时，整体本金收益反而下降。训练净收益-3,000U、验证+4,600U，也提示效果会随时段变化。',
 '当批近似检验中，这个稀疏候选的“投入ROI＋条件命中率”BH q约0.005、额外BY修正约0.031，具有进一步研究价值；但这是复用历史后的探索性结果，不是独立前向验证，也不是下一期会有52.32%命中概率的保证。它不是训练段预先选出的胜者。',
 '## 资金限制会改变实际收益比较',
 '进一步以冻结时固定买双的参数做独立执行账户试算：初始100,000U、每笔100U、单日亏损限制3,000U、最大回撤15,000U、最高单额400U。门禁必须在资金和金额状态更新之前生效，独立100U影子信号仍持续产生。',
 table(['区间／同初始本金独立账户','不加形态门禁净收益U','组合＋突破确认净收益U','原→筛选命中率','原→筛选回撤U'],[
 ['末段重新开户比较',n(risk['sensitivity']['replication_fresh_account']['baseline']['net']),n(risk['sensitivity']['replication_fresh_account']['pattern_gate']['net']),pct(risk['sensitivity']['replication_fresh_account']['baseline']['prediction_hit'])+' → '+pct(risk['sensitivity']['replication_fresh_account']['pattern_gate']['prediction_hit']),'15,000 → 2,800'],
 ['全历史连续账户',n(risk['sensitivity']['full_history_continuous_account']['baseline']['net']),n(risk['sensitivity']['full_history_continuous_account']['pattern_gate']['net']),pct(risk['sensitivity']['full_history_continuous_account']['baseline']['prediction_hit'])+' → '+pct(risk['sensitivity']['full_history_continuous_account']['pattern_gate']['prediction_hit']),'15,000 → 8,700']]),
 '原因是原始账户会触及日亏损／回撤限制，提前停单；过滤账户没有触及这些限制。这说明形态门禁可能通过避免过早触发资金停单改善本金收益，而不一定是大幅提高原始预测能力。它仍是指定条件下的敏感性试算，不能当作本站当前生产会员的真实收益。末段重新开户与全历史连续账户是两种不同情景，不能相加。',
 '**现有执行层区别：** 普通模拟金额订阅可能受源策略自身资金停单影响；金额方案订阅可使用独立信号。若源策略已先停止产生可执行订单，只在机器人订阅端做过滤不能复现上表。需要明确采用独立策略副本或执行账本，让形态门禁决定哪些预测进入该账本；不能自动解除其他资金风控。',
 '## 六种策略的结论',
 table(['基础策略','本轮发现','处理建议'],[
 ['上期大小跟随','训练选出的1小时吞没门禁通过中段验证，但末段净收益36,300→18,400U且命中率下降；五段滚动却有小幅改善','时段分歧，保留原始对照，不统一套反转开关'],
 ['上期单双跟随','4小时分型＋位置提高条件命中率，但末段总净收益下降，前置验证未通过','偏向回撤／暴露控制研究，不认定为盈利增强'],
 ['固定买大','1小时组合底部＋位置在三段均为正、末段由亏转盈','加入新数据观察清单，不能据此保证转正'],
 ['固定买小','4小时组合底部＋位置在三段均为正，末段收益与命中率都提高','优先观察候选之一'],
 ['固定买单','多数训练阶段或滚动结果不稳定，部分规则只是减亏','没有形成可推广的稳定入场点'],
 ['固定买双','4小时星线＋位置三段均为正；更稀疏的组合突破确认提高效率但牺牲无资金限制下的总收益','分别验证“整体收益组”和“低暴露高效率组”，不混淆目标']]),
 '## 与当前系统结合的执行方案',
 '1. 信号层、形态分析层和执行账本分离。六种基础策略照常生成100U独立预测；无论机器人暂停与否都继续形成K线，避免暂停后没有新数据而无法恢复。',
 '2. 配置明确选择：基础策略、1h／4h、分型／星线／吞没／组合、确认即用／位置过滤／后续突破确认、20根BOLL参数、失效低点退出和资金限制。观察与自动两种模式分开，新配置默认观察。',
 '3. 每个信号同时保存“图上参考位置”和“实际确认收盘时间”。三根分型必须等右侧收盘，五根必须等右侧两根；突破确认必须是后续K线。待确认最多3根，失效或超时清除。',
 '4. 门禁在执行账本下单前检查，实际延迟发送前再检查；已经下注的订单照常结算，过期消息不补发。暂停恢复不擅自清零本金、下注金额或金额方案计数，其他风险限制继续生效。',
 '5. 先冻结上面的3个主要候选与1个高效率候选，按机器人／账号订阅独立观察。今后新数据才是新的前向样本；建议至少30—60天，并积累足够完整启停轮次及500—1,000条被放行预测，稀疏规则应继续延长。时间和笔数只是运营观察下限，不保证统计充分。',
 '6. 同时比较本金收益率、投入收益率、条件命中率、盈利单率、净收益、回撤、放行比例、启停轮次和失败确认率。用同一日期、同一初始本金、同样金额与资金约束做对照；不能靠把稀疏策略下注额放大约7.7倍来凑总投入。',
 '7. 最终验证会员实际金额方案、并发资金占用、失败推送和消息有效期。不能把几种单独模拟的收益相加后只除以一份本金，也不能把固定大／小或单／双当成独立预测来源。',
 '当前这些具体形态只在本地研究中实现，尚未接入生产K线自动标记或实际推送门禁；原有趋势风控的简化顶部／底部判断不等于本次全部形态。',
 '## 范围、检验与局限',
 '沿用冻结的130,260期开奖结果，对重复小时10期作只读修正，过滤首条无法取得前期及休息时段信号，每个基础策略129,611条。按原期号前60%训练、中20%验证、后20%复核；后段约66个自然日。该历史已在此前研究中查看，不称为从未见过的数据。',
 '25个模型／策略：原始对照＋4种形态家族×2种周期×3种入场模式，共150项比较。三根分型、适配星线、吞没及组合用于门禁；五根分形仅作诊断。总计288项“净收益或投入ROI＋命中率”的近似联合检验，报告BH及BY校正；源开奖共享，置信区间按日4日区块计算，仍存在小样本、时段变化与研究选择偏差。',
 '每种形态的后5／20笔结果，按各自区间内不重叠事件统计；不同形态／策略仍相关。这些表用于描述，不据此把后段最佳参数直接当成执行方案。没有“形态出现就必胜”的结论。',
 '所有金额都是本站按保存赔率计算的假设结算，未取得外部成交回执。信号生成与结算基准延迟15秒，不代表真实发送时刻。没有交易费用、会员实际加减金额或网络失败回执时，不能声称实际盈利已提高。',
 '研究脚本：scripts/reversal_pattern_rules.py、scripts/analyze_candle_reversals.py、scripts/candle_reversal_risk_sensitivity.py。全部模型、资金试算、形态时间及入出场标记保存在data/audits/candle-reversals-20261121。',
 '## 全模型结果（包含未胜出规则）'
]
comparison=[]
for r in study['results']:
 rows=[]
 for key,v in r['models'].items():
  z=v['replication'];d=r['diagnostics'].get(key,{});rows.append([name(key),n(v['train']['net']),n(v['validation']['net']),n(z['net']),pct(z['roi']),pct(z['prediction_hit']),n(z['max_dd']),n(z['bets'])])
  comparison.append({'策略':r['name'],'规则':name(key),'训练净收益U':v['train']['net'],'验证净收益U':v['validation']['net'],'复核净收益U':z['net'],'复核本金收益率':z['net']/r['initial_capital'],'复核投入收益率':z['roi'],'复核命中率':z['prediction_hit'],'复核最大回撤U':z['max_dd'],'复核笔数':z['bets'],'本金与命中联合BH_q':d.get('q_joint_capital'),'效率与命中联合BH_q':d.get('q_joint_efficiency'),'本金与命中联合BY_q':d.get('q_by_joint_capital'),'效率与命中联合BY_q':d.get('q_by_joint_efficiency')})
 blocks+=['### '+r['name'],table(['规则','训练净收益U','验证净收益U','复核净收益U','复核投入ROI','复核命中率','复核回撤U','复核笔数'],rows)]
 pattern_rows=[]
 for hours,data in r['shape_outcomes'].items():
  for key,value in data.items():
   e=value['replication']['20'];pattern_rows.append([hours,PATTERNS[key],value['raw_confirmed_count'],e.get('events',0),n(e.get('mean_net')),pct(e.get('hit'))])
 blocks.append(table(['周期','形态','全范围确认次数','末段非重叠20笔事件数','其后20笔平均收益U','其后20笔命中率'],pattern_rows))
blocks+=['## 补充资金情景（各自独立账户，不能相加）']
capital_rows=[]
for case in risk['other_account_cases']:
 for scope,value in case['scopes'].items():
  b,z=value['baseline'],value['pattern_gate'];capital_rows.append([case['strategy'],name(case['model']),'末段重新开户' if scope=='replication_fresh_account' else '全范围连续账户',n(b['net'])+' → '+n(z['net']),pct(b['capital_return'])+' → '+pct(z['capital_return']),pct(b['prediction_hit'])+' → '+pct(z['prediction_hit']),n(b['max_dd'])+' → '+n(z['max_dd'])])
blocks.append(table(['策略','候选','情景','净收益U：原→筛选','本金收益率：原→筛选','命中率：原→筛选','回撤U：原→筛选'],capital_rows))
report=ROOT/'docs/反转形态与六类策略进出场专项方案-20261121.txt';report.write_text('\n\n'.join(blocks)+'\n',encoding='utf8')
with (OUT/'全形态规则对比.csv').open('w',encoding='utf-8-sig',newline='') as stream:
 w=csv.DictWriter(stream,fieldnames=list(comparison[0]));w.writeheader();w.writerows(comparison)
prior,data=frames();entry_rows=[];occurrence_rows=[];watchset=set(WATCH+[('fixed_even','4h.union.confirm')])
for identity,f in data:
 for hours in (1,4):
  b=bars_for(f,hours);feat=detect(b);last=float(f.decision.max())
  for key,flag in feat['patterns'].items():
   if key not in PATTERNS:continue
   for j in np.flatnonzero(flag&feat['known']&(b.end.to_numpy()<=last)):
    center=j-1 if key in ('top3','bottom3','morning_nogap','evening_nogap','morning_gap','evening_gap') else j-2 if key in ('top5','bottom5') else j
    first=int(np.searchsorted(f.decision.to_numpy(),float(b.end.iloc[j]),side='left'));known=int(np.searchsorted(f.settled.to_numpy(),float(b.end.iloc[j]),side='left')-1)
    occurrence_rows.append({'策略':identity['name'],'周期':str(hours)+'小时','形态':PATTERNS[key],'形态参考K线开始_北京':bj(b.index[center].timestamp()),'确认收盘_北京':bj(b.end.iloc[j]),'最早影响的新预测期号':int(f.draw.iloc[first]) if first<len(f) else None,'形态已知信号截止期号':int(f.draw.iloc[known]) if known>=0 else None,'确认时累计信号收益U':float(b.close.iloc[j]),'前窗口支撑U':float(feat['support'][j]),'前窗口压力U':float(feat['resistance'][j]),'BOLL下轨U':float(feat['lower'][j]),'BOLL中轨U':float(feat['middle'][j]),'BOLL上轨U':float(feat['upper'][j])})
 for sid,key in watchset:
  if sid!=identity['id']:continue
  h,family,mode=key.split('.');b=bars_for(f,int(h[:-1]));_,events=policy(f,b,detect(b),family,mode,True)
  for e in events:
   center=max(0,e['pattern_center_index']);stamp=float(__import__('pandas').Timestamp(e['time']).timestamp());first=int(np.searchsorted(f.decision.to_numpy(),stamp,side='left'));known=int(np.searchsorted(f.settled.to_numpy(),stamp,side='left')-1)
   entry_rows.append({'策略':identity['name'],'候选规则':name(key),'形态触发K线开始_北京':bj(b.index[center].timestamp()),'允许使用时间_北京':bj(stamp),'最早影响的新预测期号':int(f.draw.iloc[first]) if first<len(f) else None,'形态已知信号截止期号':int(f.draw.iloc[known]) if known>=0 else None,'动作':'停止后续订单' if e['action']=='pause' else '允许下一条新信号','原因':e['reason'],'累计信号收益U':e['close'],'形态失效低点U':e['setup_low'],'前窗口支撑U':e['support'],'前窗口压力U':e['resistance'],'BOLL下轨U':e['boll_lower'],'BOLL中轨U':e['boll_middle'],'BOLL上轨U':e['boll_upper']})
for filename,rows in [('已确认形态明细.csv',occurrence_rows),('候选入出场标记.csv',entry_rows)]:
 with (OUT/filename).open('w',encoding='utf-8-sig',newline='') as stream:
  w=csv.DictWriter(stream,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
print(json.dumps({'report':str(report),'models':len(comparison),'patterns':len(occurrence_rows),'entry_exit_markers':len(entry_rows)},ensure_ascii=False))
