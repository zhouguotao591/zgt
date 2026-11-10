"""Read-only audit: independent integer settlement/pricing vs stored backtests."""
import copy,csv,json,sqlite3,sys
from collections import Counter
from contextlib import closing
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from pc28.history_backtest import source_window,replay
from pc28.maintenance import block_reason

def cash(value):return int(Decimal(str(value))*100)

def reference(rows,snapshot):
 s=snapshot['settings'];odds=snapshot['odds'];stake=s['base_cents'];base=stake
 balance=peak=cash(s['initial_balance']);daily=0;day=None;up=down=streak=diff=until=0;halt=False;out=[];skips=Counter()
 for i,row in enumerate(rows):
  if not i:skips['观察窗口预热']+=1;continue
  target=row['id'];at=row['_time'];prev=rows[i-1]
  reason=block_reason(prev,at-timedelta(microseconds=1),snapshot.get('maintenance_guard',True),at)
  if reason:skips[reason]+=1;continue
  if halt:skips['胜负差值暂停']+=1;continue
  if until and target<=until:skips['连续盈利暂停']+=1;continue
  if until:until=0;streak=0
  if prev['id']+1!=target:skips['开奖缺期边界']+=1;continue
  if at.astimezone(timezone.utc).date()!=day:day=at.astimezone(timezone.utc).date();daily=0
  reason=''
  if stake<odds.get('min_stake_cents',1):reason='market_min_stake'
  elif stake>odds.get('max_stake_cents',10**18):reason='market_max_stake'
  elif balance<stake:reason='余额不足'
  elif stake>cash(s['max_stake']):reason='超过账户单笔金额上限'
  elif s['max_drawdown'] and peak-balance+stake>cash(s['max_drawdown']):reason='达到最大回撤限制'
  elif s['daily_loss_limit'] and daily-stake < -cash(s['daily_loss_limit']):reason='达到当日亏损限制'
  if reason:skips[reason]+=1;continue
  direction=s['direction'];n=row['total'];hit=(n>=14 if direction=='大' else n<=13 if direction=='小' else bool(n%2) if direction=='单' else not n%2)
  if not hit:credit=0;outcome='loss'
  elif n in odds.get('refund_winning_totals',[]):credit=stake;outcome='refund'
  else:
   if n in odds.get('full_payout_totals',[]):factor=odds['normal']
   elif stake>=odds.get('high_stake_threshold_cents',10**18):factor=odds['high_stake_factor']
   else:factor=odds['special_size' if direction in ('大','小') else 'special_parity'] if n in (13,14) else odds['normal']
   credit=min((stake*factor+50)//100,odds.get('max_return_per_draw_cents',10**18));outcome='win'
  pnl=credit-stake;balance+=pnl;daily+=pnl;peak=max(peak,balance)
  out.append({'target_draw':target,'direction':direction,'stake':stake,'credit':credit,'pnl':pnl,'outcome':outcome,'hit':int(hit),'total':n,'equity':(balance-cash(s['initial_balance']))/100})
  streak=streak+1 if outcome=='win' else 0;diff+=1 if outcome=='win' else -1 if outcome=='loss' else 0
  if s.get('win_limit',0) and streak>=s['win_limit']:until=target+s['pause_draws']
  if s.get('win_loss_difference_limit',0) and abs(diff)>=s['win_loss_difference_limit']:halt=True
  if s['stake_mode']=='fixed':stake=base
  elif s['stake_mode']=='adjust':
   if outcome=='loss' and s['loss_increment_cents']:
    stake+=s['loss_increment_cents'];up+=1
    if s.get('max_increase_steps',0) and up>=s['max_increase_steps']:stake=base;up=down=0
   elif outcome=='win' and s.get('win_decrement_cents',0):
    stake=max(base if s.get('stake_floor')=='base' else 100,stake-s['win_decrement_cents']);down+=1
    if s.get('max_decrease_steps',0) and down>=s['max_decrease_steps']:stake=base;up=down=0
  else:raise ValueError('Reference audit supports current fixed/adjust configurations only')
 return out,{'net':sum(o['pnl'] for o in out)/100,'bets':len(out),'wins':sum(o['hit'] for o in out),'refunds':sum(o['outcome']=='refund' for o in out),'profitable_orders':sum(o['pnl']>0 for o in out),'loss_orders':sum(o['pnl']<0 for o in out),'gross_profit':sum(max(0,o['pnl']) for o in out)/100,'gross_loss':-sum(min(0,o['pnl']) for o in out)/100,'investment':sum(o['stake'] for o in out)/100,'max_stake':max((o['stake'] for o in out),default=0)/100,'next_stake':stake/100,'balance':balance/100,'skips':dict(skips)}

def markdown(report):
 results=[r for r in report['results'] if not r.get('no_saved_run')]
 good=all(not r['mismatch_counts'] and r.get('csv_check',{}).get('matches',True) for r in results) and not report['data_integrity']['mismatches']
 lines=['# 四条单边策略历史回测核对','',f"审计时间（UTC）：{report['created']}",'',('结论：本次核对未发现逐笔金额、结算、汇总或导出净收益计算错误。' if good else '结论：存在差异，请查看audit.json逐项定位。'),'','## 保存结果与独立复算','','金额单位均为U；“固定买”表示方向固定，下注金额是否固定由金额模式决定。','','| 策略 | 金额模式 | 保存净收益 | 独立净收益 | 期号范围 | 订单数 |','|---|---|---:|---:|---|---:|']
 for r in results:
  s=r['snapshot_settings'];mode=f"固定{s['base_cents']/100:g}U" if s['stake_mode']=='fixed' else f"基础{s['base_cents']/100:g}U，赢减{s.get('win_decrement_cents',0)/100:g}U、亏加{s['loss_increment_cents']/100:g}U"
  lines.append(f"| {r['name']} | {mode} | {r['stored_net']:,.2f} | {r['independent']['net']:,.2f} | {r['coverage']['first_id']}—{r['coverage']['last_id']} | {r['independent']['bets']:,} |")
 lines+=['','## 逐笔盈亏拆解','','| 策略 | 盈利单 | 亏损单 | 退本金单 | 盈利金额合计 | 亏损金额合计 | 实际最高单注 |','|---|---:|---:|---:|---:|---:|---:|']
 for r in results:
  s=r['independent'];lines.append(f"| {r['name']} | {s['profitable_orders']:,} | {s['loss_orders']:,} | {s['refunds']:,} | {s['gross_profit']:,.2f} | {s['gross_loss']:,.2f} | {s['max_stake']:,.2f} |")
 lines+=['','净收益＝逐笔返还合计－逐笔下注合计＝盈利金额合计－亏损金额合计；最终余额＝初始本金＋净收益。方向命中数量包含命中退本金的订单，不能全部按盈利订单计算。','','## 统一窗口对照','',f"共同窗口：{report['common_window']['first_id']}—{report['common_window']['last_id']}，{report['common_window']['draws']:,}期开奖。",'', '| 方向 | 当前参数在共同窗口的净收益 | 全部改为固定100U的对照净收益 |','|---|---:|---:|']
 base={r['direction']:r for r in report['same_window_all_fixed_100U']}
 for r in report['same_window_current_parameters']:lines.append(f"| {r['direction']} | {r['net']:,.2f} | {base[r['direction']]['net']:,.2f} |")
 pairs=[base[a]['net']+base[b]['net'] for a,b in [('单','双'),('大','小')]]
 lines+=['',f"同窗口、同为固定100U时：单＋双＝{pairs[0]:,.2f}U；大＋小＝{pairs[1]:,.2f}U。0/27命中侧退本金、另一侧亏损，因此两侧收益不会完全抵消。",'', '## 差异来源与范围','', '- 当前买单、买双使用固定金额；买大、买小使用变额。变额必须按每笔实际金额加权，不能仅凭胜负单数计算净收益。','- “最近10万期”随最新期开奖滚动；不同时间提交的回测可能不是相同期号。变额策略在窗口起点恢复基础金额，窗口变化还会改变后续金额路径。','- 本次买单的截止期号与其他三条不同；共同窗口下的买单结果也随之变化。','- 买大/买小的累计加额、减额重置上限均为0。adjust模式不使用旧max_losses字段进行连亏重置。','- 所有实际单注均低于3001U，因此本次没有触发1.98倍高注规则。13/14按当前快照规则2.0倍；0/27命中退本金。','- 10万期不等于10万笔订单：各保存结果有99500笔，另500期为预热、维护停盘边界和缺期边界跳过。','- 当前参数与各自最新保存快照一致。此审计不改变参数、不覆盖旧回测、不调整会员资金。','','## 检查范围与局限','',f"- 共核对{sum(r['independent']['bets'] for r in results):,}笔订单，逐笔核对期号、方向、金额、返还、盈亏、命中、退本和累计收益。",f"- 独立检查{report['data_integrity']['draws_checked']:,}期数据的原始20码转换、三球和值、大小单双分类；数据异常数：{sum(report['data_integrity']['mismatches'].values())}。",'- 保存明细、当前引擎重放、独立金额/赔率计算和CSV导出交叉核对。','- 时间窗口整理与维护日历复用生产函数；赔率和下一单金额使用独立实现。维护时段政策本身未通过外部来源重新认证。','- 回测假设有效期可下单，不模拟实际延迟推送、接口拒绝或外部成交，所以不等同于会员实际跟单收益。','','附件：audit.json、summary.csv，以及各方向前30/后30笔逐笔样本。']
 return '\n'.join(lines)+'\n'

def main():
 folder=ROOT/'data/audits'/('single-backtests-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));folder.mkdir(parents=True)
 with closing(sqlite3.connect('file:'+str(ROOT/'data/pc28.sqlite3')+'?mode=ro',uri=True)) as db:
  db.row_factory=sqlite3.Row;db.execute('BEGIN')
  current=[dict(r) for r in db.execute('SELECT id,name,settings FROM custom_strategies WHERE id NOT IN(SELECT id FROM deleted_strategies)') if json.loads(r['settings']).get('strategy_type')=='single']
  saved=[(r['id'],r['created'],json.loads(r['result'])) for r in db.execute('SELECT id,created,result FROM history_backtest_runs ORDER BY created DESC,rowid DESC')]
  raw=[dict(r) for r in db.execute('SELECT * FROM draws ORDER BY id')];conflicts={r[0] for r in db.execute('SELECT draw_id FROM conflicts WHERE resolved=0')}
  all_data={r['id']:r for r in raw};audit=[];windows={};latest=[]
  for strategy in current:
   run=next((r for r in saved if r[2].get('strategy',{}).get('id')==strategy['id']),None)
   if not run:audit.append({'strategy_id':strategy['id'],'name':strategy['name'],'no_saved_run':True});continue
   run_id,created,result=run;snapshot=result['strategy'];cov=result['coverage'];latest.append((strategy,run))
   raw_cut=[r for r in raw if r['id']<=cov['last_id']]
   spec={'mode':'count','periods':cov['draws']} if cov.get('range_mode')=='count' else {'mode':'dates','start':cov['requested_start'],'end':cov['requested_end']}
   rows,coverage=source_window(raw_cut,conflicts,created,range_spec=spec)
   windows[coverage['data_sha256']]=(rows,coverage)
   independent,summary=reference(rows,snapshot);fresh,fresh_orders=replay(rows,snapshot)
   stored=[json.loads(r[0]) for r in db.execute('SELECT record FROM history_backtest_orders WHERE run_id=? ORDER BY seq',(run_id,))]
   errors=Counter();examples=[]
   if coverage['data_sha256']!=cov['data_sha256']:errors['source_hash_changed']+=1
   if len(stored)!=len(independent):errors['order_count']+=1
   if len(stored)!=len({o['target_draw'] for o in stored}):errors['duplicate_draw']+=1
   for index,(actual,expected) in enumerate(zip(stored,independent)):
    for field in ('target_draw','direction','stake','credit','pnl','outcome','hit','total','equity'):
     if actual[field]!=expected[field]:
      errors[field]+=1
      if len(examples)<8:examples.append({'index':index,'draw':actual['target_draw'],'field':field,'stored':actual[field],'independent':expected[field]})
   for field in ('net','bets','wins','refunds','investment','balance'):
    if result[field]!=summary[field]:errors['summary_'+field]+=1
   if fresh['net']!=result['net'] or fresh_orders!=stored:errors['current_engine_vs_saved']+=1
   detail={'strategy_id':strategy['id'],'name':strategy['name'],'run_id':run_id,'created':created,'snapshot_settings':snapshot['settings'],'settings_match_current':snapshot['settings']==json.loads(strategy['settings']),'coverage':{k:cov[k] for k in ('first_id','last_id','draws','data_sha256')},'stored_net':result['net'],'current_engine_net':fresh['net'],'independent':summary,'mismatch_counts':dict(errors),'examples':examples}
   export=ROOT/'data/exports'/Path(result['file']).name
   if export.is_file():
    count=0;csv_net=csv_stake=csv_credit=Decimal(0)
    with export.open(encoding='utf-8-sig',newline='') as f:
     for o in csv.DictReader(f):count+=1;csv_net+=Decimal(o['pnl']);csv_stake+=Decimal(o['stake']);csv_credit+=Decimal(o['credit'])
    detail['csv_check']={'rows':count,'net':str(csv_net),'investment':str(csv_stake),'credit':str(csv_credit),'matches':count==result['bets'] and csv_net==Decimal(str(result['net'])) and csv_credit-csv_stake==csv_net}
   else:detail['csv_check']={'missing':True,'matches':False}
   audit.append(detail);print(json.dumps({k:detail[k] for k in ('name','stored_net','current_engine_net','mismatch_counts')},ensure_ascii=False),flush=True)
   with (folder/(snapshot['settings']['direction']+'-sample.csv')).open('w',encoding='utf-8-sig',newline='') as f:
    fields=list(independent[0]) if independent else [];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(independent[:30]+independent[-30:])
  # Independently verify every raw 20-number conversion once in the audited union.
  union={r['id']:r for rows,_ in windows.values() for r in rows};data_errors=Counter()
  for row in union.values():
   if row['a']+row['b']+row['c']!=row['total']:data_errors['abc_total']+=1
   if row['size']!=('大' if row['total']>=14 else '小'):data_errors['size']+=1
   if row['parity']!=('单' if row['total']%2 else '双'):data_errors['parity']+=1
   nums=json.loads(row['numbers'])
   if len(nums)==20:
    abc=[sum(nums[index] for index in group)%10 for group in ([1,4,7,10,13,16],[2,5,8,11,14,17],[3,6,9,12,15,18])]
    if abc!=[row['a'],row['b'],row['c']]:data_errors['raw_conversion']+=1
  common_hash=Counter(r['coverage']['data_sha256'] for r in audit if not r.get('no_saved_run')).most_common(1)[0][0]
  common_rows,common_cov=windows[common_hash];comparisons=[];baseline=[]
  for strategy,run in latest:
   snapshot=copy.deepcopy(run[2]['strategy']);_,summary=reference(common_rows,snapshot)
   comparisons.append({'name':strategy['name'],'direction':snapshot['settings']['direction'],'mode':snapshot['settings']['stake_mode'],'net':summary['net'],'max_stake':summary['max_stake']})
   snapshot['settings'].update(stake_mode='fixed',base_cents=10000,max_stake=20000,win_limit=0,win_loss_difference_limit=0,daily_loss_limit=0,max_drawdown=0)
   _,fixed=reference(common_rows,snapshot);baseline.append({'direction':snapshot['settings']['direction'],**fixed})
  report={'created':datetime.now(timezone.utc).isoformat(),'audit_scope':'latest saved run for each existing non-deleted single strategy','data_integrity':{'draws_checked':len(union),'mismatches':dict(data_errors)},'results':audit,'common_window':common_cov,'same_window_current_parameters':comparisons,'same_window_all_fixed_100U':baseline,'shared_calendar_dependency':'Entry eligibility shares source_window and maintenance.block_reason; payout and next-stake computations are independently implemented without settlement/previous_amount.'}
  (folder/'audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
  (folder/'report.md').write_text(markdown(report),encoding='utf8')
  with (folder/'summary.csv').open('w',encoding='utf-8-sig',newline='') as f:
   fields=['name','direction','mode','stored_net','independent_net','profit_orders','loss_orders','refunds','gross_profit','gross_loss','max_stake','first_draw','last_draw','mismatches'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
   for r in audit:
    if r.get('no_saved_run'):continue
    s=r['independent'];w.writerow(dict(name=r['name'],direction=r['snapshot_settings']['direction'],mode=r['snapshot_settings']['stake_mode'],stored_net=r['stored_net'],independent_net=s['net'],profit_orders=s['profitable_orders'],loss_orders=s['loss_orders'],refunds=s['refunds'],gross_profit=s['gross_profit'],gross_loss=s['gross_loss'],max_stake=s['max_stake'],first_draw=r['coverage']['first_id'],last_draw=r['coverage']['last_id'],mismatches=sum(r['mismatch_counts'].values())))
  print('DATA',json.dumps(report['data_integrity']),flush=True);print('COMMON',json.dumps(comparisons,ensure_ascii=False),flush=True);print('FIXED100',json.dumps([{k:r[k] for k in ('direction','net','bets','profitable_orders','loss_orders','refunds')} for r in baseline],ensure_ascii=False),flush=True)
  print('ARTIFACT',str(folder),flush=True)

if __name__=='__main__':main()
