import json,csv
from pathlib import Path
from datetime import datetime,timedelta
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'data/audits/six-strategy-entries-20261121';study=json.loads((OUT/'study.json').read_text(encoding='utf8'))
RULES={'always':'不新增门禁','1h20_p2r2':'1小时20根，停2启2','4h20_p2r2':'4小时20根，停2启2','1h20_p1r2':'1小时20根，停1启2','1h40_p2r3':'1小时40根，停2启3','4h40_p2r3':'4小时40根，停2启3','momentum_1h':'1小时趋势延续','momentum_4h':'4小时趋势延续','reentry_lower_1h':'1小时下轨修复','breakout_1h':'1小时压力突破','dual_4h_1h':'4小时趋势＋1小时确认','accuracy_40_20':'近40/20笔命中率门禁'}
SHAPES={'top':'顶部反转','bottom':'底部反转','up':'上升＋中轨上方','down':'下降＋中轨下方','support_break':'跌破前窗口支撑','resistance_break':'突破前窗口压力','boll_upper_break':'BOLL上轨外','boll_lower_break':'BOLL下轨外','bullish_engulf':'看涨吞没','bearish_engulf':'看跌吞没','hammer':'长下影线','shooting_star':'长上影线'}
DETAILS={
 'follow_size':'不建议直接套用本次训练选出的常规门禁：末段ROI和命中率都略降，净收益减少21,700U。5段滚动中ROI略有提高但总收益接近不变，说明效果随时段变化。下轨修复在末段ROI较好，但训练段为负且末段只保留约15%的预测，不能事后当作最优规则。当前适合作为不新增门禁的对照组。',
 'follow_parity':'列为降低回撤、提高投入效率的观察候选。1小时40根、停2启3的末段ROI由0.93%到1.21%，命中率由50.50%到50.64%，回撤由11,900U到5,600U，但净收益由24,100U降到17,700U，前置验证未通过，5段滚动也未稳定改善。',
 'fixed_big':'没有得到可推广的入出场规律。训练选出的门禁使末段ROI由-0.32%到-1.01%，命中率下降。下轨修复末段盈利，但训练段ROI为负；不能因为看到后面反弹就把历史低点当成可提前识别的入场点。',
 'fixed_small':'没有得到稳健的入出场规律。训练段选出的40/20笔命中门禁把末段小幅正收益变成亏损；下轨修复在训练、验证、末段均为正，但训练优势很小、末段保留不足20%预测，未按本次既定流程胜出，适合单独冻结后验证而非直接上线。',
 'fixed_odd':'只能看到减亏线索，不能称为盈利方案。1小时20根、停1启2减少末段亏损，但ROI仍为负、条件命中率仍未到50%。不应把减少负收益订单解释为已经形成可持续的盈利入场点。',
 'fixed_even':'本轮最值得优先观察的效率改善线索。1小时20根、停1启2把末段ROI由0.91%到2.00%，命中率由50.51%到51.06%，回撤由19,100U到8,000U；代价是只保留约41%预测、净收益减少2,200U。该候选仍未通过前置验证，5段滚动整体也未改善。更高的末段双周期收益来自训练/验证均亏损的规则，不能拿来当确定的最优参数。'
}
def num(v):return '—' if v is None else f'{v:,.0f}'
def pct(v):return '—' if v is None else f'{v*100:.2f}%'
def pp(v):return '—' if v is None else f'{v*100:+.2f}个百分点'
def table(headers,rows):return '| '+' | '.join(headers)+' |\n| '+' | '.join(['---']*len(headers))+' |\n'+'\n'.join('| '+' | '.join(str(v).replace('|','／') for v in row)+' |' for row in rows)
def bj(s):return (datetime.fromisoformat(s)+timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
rows=[];roll=[];whole=[];capital_rows=[]
for r in study['results']:
 b,f=r['baseline'],r['filtered'];rows.append([r['name'],RULES[r['candidate']],pct(b['roi'])+' → '+pct(f['roi']),pct(b['prediction_hit'])+' → '+pct(f['prediction_hit']),num(b['net'])+' → '+num(f['net']),num(b['max_dd'])+' → '+num(f['max_dd']),pct(r['coverage'])])
 w=r['walkforward'];roll.append([r['name'],pct(w['baseline']['roi'])+' → '+pct(w['filtered']['roi']),pct(w['baseline']['prediction_hit'])+' → '+pct(w['filtered']['prediction_hit']),num(w['baseline']['net'])+' → '+num(w['filtered']['net']),sum(f['validation_pass'] for f in w['folds'])])
 v=r['whole_baseline'];whole.append([r['name'],pct(v['roi']),pct(v['prediction_hit']),pct(v['profit_win']),num(v['net']),num(v['max_dd'])])
 cap=r['capital_objective'];capital_rows.append([r['name'],num(cap['initial_capital']),RULES[cap['candidate']],pct(cap['baseline_capital_return'])+' → '+pct(cap['filtered_capital_return']),pct(cap['baseline']['prediction_hit'])+' → '+pct(cap['filtered']['prediction_hit']),'未通过' if not cap['validation_pass'] else '通过'])
blocks=[
 '# 六类策略K线入场与停止专项评估',
 '编制日期：2026-11-21。截止3509217期，北京时间2026-11-21 16:40。仅在本地分析，未修改生产策略、金额方案、机器人订阅或风控开关。',
 '## 结论',
 '这6种简单策略可以用长期开奖记录重新构造，不必只依赖最近约9天的实时信号。本次以130,260期开奖结果为基础，复用10期时间修正及现有休息时段规则，分别得到129,611笔有效预测。比较12种门禁（含不新增门禁），并做5段时间正序滚动复核。',
 '**若目标是提高每笔投入效率、降低回撤，可优先观察固定买双，其次观察上期单双跟随。若按此前会员中心的“净收益÷初始本金”口径，则这6种策略均未找到可推广的改进。** 这是一项研究优先级，不是已经证实可自动执行的盈利建议。6个训练选出的效率候选都未通过前置验证，投入ROI改善的95%区间均跨过0；没有一组可称为稳定的最优入出场点。',
 '## 首先区分两种收益率',
 '本金收益率＝净收益÷初始本金，沿用你此前会员中心的定义。同一本金下，它与总净收益的高低完全一致。投入收益率＝净收益÷累计下单金额，用于比较每笔投入效率。少下单可能提高后者、降低前者，不能把两者混用。',
 '下面先按最大化同本金净收益在训练段选候选，验证要求净收益与命中率都改善且回撤不恶化。初始本金取冻结时这6种策略的100,000U设置；仍是纯信号统计的名义换算，没有叠加账户止损，不能视为实际账户收益。',
 table(['策略','初始本金U','训练段按本金目标选出的候选','末段名义本金收益率：原→候选','末段命中率：原→候选','前置验证'],capital_rows),
 '结论：没有一项同时满足稳定的本金收益提升与命中率提升。固定买单表现为减亏而非转为盈利；其他候选多是牺牲总收益换取较低回撤或较高投入效率。',
 '## 最后66个自然日的同区间比较',
 table(['策略','训练段选出的候选','投入收益率：原→筛选','方向命中率：原→筛选','净收益U：原→筛选','最大回撤U：原→筛选','保留预测比例'],rows),
 '本表改为按训练段投入ROI选候选，因此固定买小的候选会与上面的本金目标表不同。每笔固定100U，独立信号模拟收益，不是会员真实成交收益。方向命中包括退本金方向命中，盈利单率只计净收益大于0。过滤后投入减少，所以投入收益率提高与本金收益率提高不是同一件事。',
 '最后20%数据也在上轮研究中使用过，因此这里称为时间顺序复核段，不宣称它是从未看过的全新样本外数据。不能再根据这个区间反复挑参数后宣布有效。',
 '## 两组可以用现有界面做观察的参数',
 '### A. 固定买双：效率与回撤观察组',
 '收益来源选择标准100U独立信号；观察模式；1小时K线；窗口20根；风险确认1根；恢复确认2根；最短暂停1根；趋势阈值0.05；无成交容忍3根；未知或样本不足暂停。',
 '入场候选：已收盘收益出现底部修复，或标准化上升斜率≥0.05且收盘在BOLL中轨上方；连续2根给出安全判断后允许下一条新信号。入场之前仍须通过其他资金与时效限制。',
 '停止候选：已收盘出现顶部反转、跌破此前20根窗口支撑，或标准化下降斜率≤-0.05且收益低于BOLL中轨（底部反转例外）；1根确认即暂停新推送。中性不切换，暂停期间继续模拟，不重置金额方案。',
 '末段只保留约41%的预测，ROI约2.00%、命中率约51.06%，总净收益比不筛选少2,200U。ROI改善估计约+1.10个百分点，95%区间约-0.28至+2.37个百分点，仍不排除没有改善。',
 '### B. 上期单双跟随：回撤控制观察组',
 '收益来源选择标准100U独立信号；观察模式；1小时K线；窗口40根；风险确认2根；恢复确认3根；最短暂停1根；趋势阈值0.05；无成交容忍3根；未知或样本不足暂停。',
 '入场和停止使用与A相同的形态定义，但支撑压力及BOLL使用40根窗口，入场需连续3根安全确认，停止需连续2根风险确认。至少41根已收盘K线、窗口内至少40笔模拟结算和5根有成交K线。',
 '末段回撤约下降53%，但总净收益下降约27%。ROI改善约+0.28个百分点，95%区间约-0.62至+1.27个百分点。它主要是减少暴露、控制回撤的候选，不应称为已验证的盈利增强器。',
 '## 为什么不直接采用末段最漂亮的形态',
 '固定买双的“4小时趋势＋1小时确认”在末段ROI约3.58%、命中率约51.87%，看起来比上述A更好；但它在训练段ROI约-0.89%、验证段约-0.22%。这是形态/参数效果会随时段变化的例子，不能仅凭末段选择它执行。',
 '固定买大、固定买小的下轨修复在末段分别约1.00%、1.61%ROI，固定买双同一规则却约-0.50%。这说明同一种K线形态不能机械套在全部方向；这些结果仍只是研究线索。',
 '## 五段滚动复核',
 '每段都只用更早的数据训练，随后一段验证，再评价下一段；验证不通过时保留原始对照、不新增门禁。五个评价区间彼此不重叠，总计64,806笔基准预测、163个自然日。它检验的是选参流程稳定性，不等同于从历史最早时起就真正冻结并运行的前向实验。',
 table(['策略','滚动ROI：原→筛选','滚动命中率：原→筛选','滚动净收益U：原→筛选','通过验证的折数／5'],roll),
 '上期大小跟随的滚动效率略升、末段却略降；固定买双末段较好、滚动整体反而下降。这样的时段分歧意味着当前不能把单段盈利当成规律。',
 '## 更长范围的方向基准',
 table(['策略','全范围ROI','方向命中率','盈利单率','净收益U','最大回撤U'],whole),
 '上述长范围累计收益不叠加会员资金限制、单日最大亏损或策略最大回撤停单；否则方向研究会被账户停单截断。特别是初始本金较小的账户，无法把这些无限额信号轨迹直接当作可执行余额轨迹。最终资金效果必须另用实际金额方案和账户限额复核。',
 '## 具体执行顺序',
 '1. 两组观察候选先冻结参数，不更改当前机器人是否下单；持续记录新数据中的“本应入场／本应停止”。这批历史数据已经查看过，今后的新数据才是新的前向验证。',
 '2. 至少积累30天、每个候选至少1,000条被保留预测，同时保留完整的不筛选对照。它们只是运营观察下限，不保证统计充分；稀疏规则必须延长时间。',
 '3. 在同一日期、同一100U信号上比较投入收益率、条件命中率、盈利单率、总净收益、总投入、回撤、放行比例、最长连亏。需要明确运营目标：追求更高资金效率，还是更高总净收益，不能混用两者。',
 '4. 只有新数据中改善方向一致、置信区间与滚动时段表现支持，才进一步用会员实际金额方案、初始本金、日亏损及回撤限制模拟；先小范围订阅，再考虑扩大。不能靠亏后加码制造“预测更准”的结论。',
 '5. 出场仅指停止后续新推送，不是假设能撤销已发送或已下注订单。恢复不重置原金额。门禁须在信号捕获和延迟实际发送前都检查，过期订单不补发。',
 '## 每种策略详细结果'
]
csv_rows=[];event_rows=[]
for r in study['results']:
 blocks+=['### '+r['name'],DETAILS[r['id']]]
 model_rows=[]
 for key,v in r['all_candidates'].items():
  t,z,f=v['train'],v['validation'],v['replication'];model_rows.append([RULES[key],pct(t['roi']),pct(z['roi']),pct(f['roi']),pct(f['prediction_hit']),num(f['net']),num(f['max_dd']),num(f['bets'])])
  csv_rows.append({'策略':r['name'],'规则':RULES[key],'训练ROI':t['roi'],'验证ROI':z['roi'],'末段ROI':f['roi'],'末段命中率':f['prediction_hit'],'末段净收益U':f['net'],'末段回撤U':f['max_dd'],'末段笔数':f['bets'],'训练选中':key==r['candidate']})
 blocks.append(table(['规则','训练ROI','验证ROI','末段ROI','末段命中率','末段净收益U','末段回撤U','末段笔数'],model_rows))
 shapes=[]
 for key,v in r['event_patterns'].items():
  if v.get('events'):shapes.append([SHAPES[key],v['events'],f"{v['next20_mean_net']:+.2f}",pct(v['next20_hit'])])
 blocks+=['形态出现后20笔的描述性结果（并非按此选择最佳规则的许可）：',table(['形态','非重叠事件数','其后20笔平均收益U','其后20笔命中率'],shapes)]
 if r['events']:
  examples=[]
  for e in r['events']:
   info=e.get('analysis',{});boll=info.get('boll',{});row={'策略':r['name'],'候选规则':RULES[r['candidate']],'收盘时间_北京':bj(e['time']),'动作':'停止' if e['action']=='pause' else '恢复进场','原因':e.get('reason','命中率条件'),'累计信号收益U':e.get('signal_equity'),'BOLL中轨U':boll.get('middle'),'BOLL上轨U':boll.get('upper'),'BOLL下轨U':boll.get('lower'),'前窗口支撑U':info.get('support'),'前窗口压力U':info.get('resistance'),'标准化斜率':info.get('normalized_slope'),'前40笔命中率':e.get('previous40_hit'),'前20笔命中率':e.get('previous20_hit')};event_rows.append(row)
  for e in r['events'][-4:]:examples.append([bj(e['time']),'停止' if e['action']=='pause' else '恢复进场',num(e.get('signal_equity')),e.get('reason','滚动命中率条件')])
  blocks+=['训练选中候选的最近历史标记示例。金额是从0累计的100U模拟收益，不是余额，不是当前交易指令。',table(['收盘时间（北京）','研究动作','累计信号收益U','原因'],examples)]
 blocks+=['5／15／45秒假设延迟敏感性（不代表实际回执）：',table(['假设信号与结算延迟','末段ROI','末段命中率','末段净收益U'],[[str(v['assumed_delay_seconds'])+'秒',pct(v['metrics']['roi']),pct(v['metrics']['prediction_hit']),num(v['metrics']['net'])] for v in r['latency_sensitivity']])]
blocks += [
 '## 规则定义与可复核边界',
 '六种现有门禁：不新增门禁；1h20停2启2；4h20停2启2；1h20停1启2；1h40停2启3；4h40停2启3。其余通用参数为0.05标准化趋势阈值、最短暂停1根、无成交容忍3根、样本不足暂停。分析与状态转换复用生产trend_risk函数。',
 '趋势延续：仅上升且收盘高于BOLL中轨作为入场，连续2根确认；顶部、跌破支撑或下降停止。分别评价1小时和4小时。',
 '下轨修复：底部反转或从BOLL下轨下方重新收回下轨且本根收涨，确认1根入场；回到中轨或跌破支撑则停止；同根同时满足停止和入场时停止优先。',
 '压力突破：收盘突破前20根压力且处于上升趋势，确认1根入场；跌回中轨下方或顶部反转停止。',
 '双周期：已收盘4小时20根窗口处于上升，1小时出现上升或底部修复，连续2根确认后入场；4小时风险／未知或1小时顶部／破支撑／下降时停止。',
 '命中率门禁：只用当时已结算的先前预测，近40笔命中≥22笔（55%）时允许跟随，近20笔命中≤9笔（45%）时停止。没有使用当前待预测那一期的结果。',
 '新增的趋势限定、下轨修复、突破、双周期及命中率门禁仅在研究脚本中计算，尚不是生产趋势风控界面可直接选择的新模式。现有界面能直接配置的是A、B这两组观察参数；本次没有实施这些设置。',
 '训练前60%，验证中20%，末段后20%。训练候选至少500笔、保留20%以上基准预测，按训练ROI选择；验证至少200笔、保留20%以上、净收益为正、ROI与命中率高于基准且回撤不恶化。候选与规则数均已保留，未隐藏未胜出的结果。',
 'ROI不确定性按日收益和投入，用4日循环区块2,000次重采样；近似4阶HAC单侧检验，再对6个训练选中候选做BH校正。多个预测共享开奖，不能将这些订单数视为独立实验次数。ROI差的区间均含0，6个候选均未通过前置验证；本轮只给观察优先级。',
 '原始预测方向没有被门禁修改，因此全量预测命中率保持原样；筛选后变化的是被跟随子集的条件命中率。若要提高全量预测质量，需要另行验证新的方向算法。',
 '来源及重现：pc28.sqlite3的已核验draws和冲突表；algorithm-paper.sqlite3的按期保存赔率；当前配置中的休息时段开关。修正10期重复小时；排除648期休息前后信号及无法取得前一期的首条记录；每策略129,611笔。前期开奖后15秒预测、目标开奖后15秒假设结算，未模拟实际机器人网络和成交回执。',
 '研究脚本：scripts/analyze_six_strategy_entries.py；完整结果data/audits/six-strategy-entries-20261121/study.json；规则对比CSV与历史标记CSV位于同目录。7项相关测试通过，包括6种新增规则的未来数据扰动检验、当前结果不可提前使用、冲突条件停止优先、ROI比较目标及相同策略对照。',
 '本次仅本地交付，没有写入外部文档服务，没有改变正在运行的系统。'
]
report=ROOT/'docs/六类策略K线入场与停止专项评估-20261121.txt';report.write_text('\n\n'.join(blocks)+'\n',encoding='utf8')
for name,rows_ in [('规则对比.csv',csv_rows),('入出场研究标记.csv',event_rows)]:
 with (OUT/name).open('w',encoding='utf-8-sig',newline='') as stream:
  writer=csv.DictWriter(stream,fieldnames=list(rows_[0]));writer.writeheader();writer.writerows(rows_)
print(json.dumps({'report':str(report),'comparison_rows':len(csv_rows),'event_rows':len(event_rows)},ensure_ascii=False))
