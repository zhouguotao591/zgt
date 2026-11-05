"""Stream-audit all million-search records, verify winners with original replay, publish improvements."""
import atexit,copy,csv,gzip,hashlib,json,math,sqlite3,time
from concurrent.futures import ProcessPoolExecutor
from reversal_rules_reference import initialize,run_reference
from pathlib import Path
from reversal_rules_prepare import FOLDER,expand,service
from pc28 import history_backtest as hb,custom_strategy,follow_strategy
from pc28.strategies import catalog,control
from pc28.model import utcnow

METRICS=('net','bets','wins','refunds','investment','roi','max_drawdown')
def check_metrics(reference,compact,label):
 for key,value in zip(METRICS,compact):
  actual=reference[key]
  assert (actual is None and value is None) or actual is not None and value is not None and abs(actual-value)<1e-8,(label,key,actual,value)

def publish():
 manifest=json.loads((FOLDER/'manifest.json').read_text('utf8'));run=json.loads((FOLDER/'winners.json').read_text('utf8'));samples=json.loads((FOLDER/'samples.json').read_text('utf8'));acceleration=json.loads((FOLDER/'acceleration-validation.json').read_text('utf8'));s=service()
 assert not acceleration['errors'];assert len(run['summaries'])==len(manifest['strategies'])
 groups=manifest['strategies'];total=0;eligible=[];audits=[]
 for name,key in manifest['rule_source_sha256'].items():assert hashlib.sha256((FOLDER.parent.parent/name).read_bytes()).hexdigest()==key,'Rule source changed during search'
 assert s.cfg['odds']==groups[0]['snapshot']['odds'],'Odds changed during search'
 assert s.cfg['collector'].get('maintenance_guard_enabled',True)==groups[0]['snapshot']['maintenance_guard'],'Calendar guard changed during search'
 for g,summary in zip(groups,run['summaries']):
  assert summary['source']==g['id'] and summary['generated']==summary['completed']==summary['unique']==manifest['per_strategy']
  previous=set((FOLDER/f"exclude-{g['position']}.txt").read_text('ascii').splitlines());seen=set();indices=bytearray(manifest['per_strategy']);count=0;qualified=0
  with gzip.open(FOLDER/summary['parameter_file'],'rt',encoding='utf8') as stream:
   for line in stream:
    row=json.loads(line);position,index,sha,t,a,b=row
    assert position==g['position'] and 0<=index<len(indices) and not indices[index]
    assert sha not in seen and sha not in previous
    indices[index]=1;seen.add(sha);count+=1
    if a[0]>0 and b[0]>0 and a[0]>g['baseline']['one']['net'] and b[0]>g['baseline']['three']['net'] and a[1]>=100 and b[1]>=300:eligible.append(row);qualified+=1
  assert count==manifest['per_strategy'] and qualified==summary['qualified']
  audits.append({'source':g['id'],'records':count,'unique':len(seen),'old_duplicates':0,'qualified':qualified});total+=count
  del seen,previous,indices
  print(json.dumps({'audited':g['name'],'records':count,'qualified':qualified},ensure_ascii=False),flush=True)
 assert total==manifest['count'] and len(eligible)==run['totalEligible']
 ranges={}
 for label,metric in [('one',(4,0)),('three',(5,0)),('drawdown',(5,6))]:
  values=[r[metric[0]][metric[1]] for r in eligible]
  ranges[label]={'min':min(values),'max':max(values)} if values else {'min':None,'max':None}
  assert ranges[label]==run['ranges'][label]
 def norm(v,bounds):return .5 if bounds['max']==bounds['min'] else (v-bounds['min'])/(bounds['max']-bounds['min'])
 def score(row):return 100*(.4*norm(row[4][0],ranges['one'])+.3*norm(row[5][0],ranges['three'])+.3*(1-norm(row[5][6],ranges['drawdown'])))
 top={}
 for row in eligible:
  key=(score(row),row[4][0],row[5][0]);pos=row[0]
  if pos not in top or key>top[pos]:top[pos]=key
 for winner in run['winners']:
  row=winner['row'];assert abs(score(row)-winner['score'])<1e-8
  assert (score(row),row[4][0],row[5][0])==top[row[0]]
 with (FOLDER/'ranking.csv').open('w',encoding='utf-8-sig',newline='') as stream:
  writer=csv.DictWriter(stream,fieldnames=['rank','strategy','candidate_index','parameter_sha256','score','one_month_before','one_month_after','three_months_before','three_months_after','three_month_max_drawdown','one_month_bets','three_month_bets','parameters']);writer.writeheader()
  for rank,row in enumerate(sorted(eligible,key=lambda r:(score(r),r[4][0],r[5][0]),reverse=True),1):
   g=groups[row[0]];writer.writerow({'rank':rank,'strategy':g['name'].split(' · ')[0],'candidate_index':row[1],'parameter_sha256':row[2],'score':score(row),'one_month_before':g['baseline']['one']['net'],'one_month_after':row[4][0],'three_months_before':g['baseline']['three']['net'],'three_months_after':row[5][0],'three_month_max_drawdown':row[5][6],'one_month_bets':row[4][1],'three_month_bets':row[5][1],'parameters':json.dumps(expand(g['base'],g['kind'],row[3]),ensure_ascii=False)})
 # Freeze verification against the original source fingerprint before modifying any strategy.
 raw=s.db.query('SELECT * FROM draws WHERE id<=? ORDER BY id',(manifest['coverage']['last_id'],));conflicts={r['draw_id'] for r in s.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')}
 rows,coverage=hb.source_window(raw,conflicts,manifest['as_of']);month,monthcoverage=hb.source_window(raw,conflicts,manifest['as_of'],months=1)
 assert coverage['data_sha256']==manifest['coverage']['data_sha256'];assert monthcoverage['data_sha256']==manifest['one_month_coverage']['data_sha256']
 # Memoize pure calendar answers; the reference replay and its trading logic stay intact.
 original_guard=hb.maintenance.block_reason;calendar_cache={}
 def calendar(prev,decision,enabled=True,planned=None):
  key=(prev['id'],decision,enabled,planned)
  if key not in calendar_cache:calendar_cache[key]=original_guard(prev,decision,enabled,planned)
  return calendar_cache[key]
 hb.maintenance.block_reason=calendar
 checks=0;verified=[]
 with ProcessPoolExecutor(max_workers=4,initializer=initialize,initargs=(rows,month,groups,s.cfg)) as pool:
  for _ in pool.map(run_reference,samples,chunksize=1):
   checks+=14
   if checks%112==0:print(json.dumps({'reference_metric_checks':checks},ensure_ascii=False),flush=True)
  for winner,(value,detail) in zip(run['winners'],pool.map(run_reference,[w['row'] for w in run['winners']],chunksize=1)):
   checks+=14;row=winner['row'];g=groups[row[0]]
   verified.append({'source':g['id'],'original_name':g['name'],'candidate_index':row[1],'parameter_sha256':row[2],'score':winner['score'],'settings':value,'baseline':g['baseline'],'reference':detail,'published':False})
   print(json.dumps({'winner_verified':g['name'],'one':detail['one']['net'],'three':detail['three']['net'],'score':winner['score']},ensure_ascii=False),flush=True)
 for name,key in manifest['rule_source_sha256'].items():assert hashlib.sha256((FOLDER.parent.parent/name).read_bytes()).hexdigest()==key,'Rule source changed before publication'
 runtime_before={r['id']:r for r in catalog(s.db,s.cfg)['strategies']}
 for g in groups:assert runtime_before[g['id']]['settings']==g['settings_before'],'Strategy parameters changed during search'
 targets={w['source'] for w in verified};transition={'created':utcnow(),'targets':sorted(targets),'all_enabled_before':{key:row['enabled'] for key,row in runtime_before.items()},'restore_enabled':{key:runtime_before[key]['enabled'] for key in targets}}
 def restore_runtime():
  current={r['id']:r for r in catalog(s.db,s.cfg)['strategies']}
  changed=[key for key,enabled in transition['all_enabled_before'].items() if key not in targets and current[key]['enabled']!=enabled]
  if changed:
   transition.update(restored=False,reason='Other runtime flags changed; preserve current user choices',changed=changed)
  else:
   for key,enabled in transition['restore_enabled'].items():
    if current[key]['enabled']!=enabled:control(s,{'id':key,'enabled':enabled})
   transition.update(restored=True,restored_at=utcnow())
  (FOLDER/'runtime-transition.json').write_text(json.dumps(transition,ensure_ascii=False,indent=2),encoding='utf8')
 (FOLDER/'runtime-transition.json').write_text(json.dumps(transition,ensure_ascii=False,indent=2),encoding='utf8');atexit.register(restore_runtime)
 for key in targets:
  if runtime_before[key]['enabled']:control(s,{'id':key,'enabled':False})
 deadline=time.monotonic()+600;last_notice=0
 while targets:
  pending=[r for r in s.db.query('SELECT strategy,COUNT(*) count FROM custom_orders WHERE status=? GROUP BY strategy',('pending',)) if r['strategy'] in targets]
  if not pending:break
  if time.monotonic()>deadline:raise TimeoutError('Existing simulated orders have not settled; original orders retained')
  if time.monotonic()-last_notice>25:print(json.dumps({'waiting_for_existing_simulated_orders':pending},ensure_ascii=False),flush=True);last_notice=time.monotonic()
  time.sleep(5)
 before=catalog(s.db,s.cfg)['strategies']
 for g in groups:
  current=next(r for r in before if r['id']==g['id']);assert current['settings']==g['settings_before'],'Strategy parameters changed during settlement'
  if g['position'] in top:assert not current['enabled'],'A strategy to update was enabled again during settlement'
 report={'engine_regression_validation':json.loads((FOLDER/'engine-regression-validation.json').read_text('utf8')),'official_engine_validation':json.loads((FOLDER/'official-engine-validation.json').read_text('utf8')),'created':utcnow(),'manifest':manifest,'audit':audits,'generation_summary':run['summaries'],'repair':run.get('repair'),'runtime_switch_changes':[{'source':g['id'],'before':g['enabled'],'current':next(r for r in before if r['id']==g['id'])['enabled']} for g in groups if g['enabled']!=next(r for r in before if r['id']==g['id'])['enabled']],'total_replayed':total,'qualified_total':len(eligible),'normalization_ranges':ranges,'formula':'100*(.4*normalized_month_net+.3*normalized_three_month_net+.3*(1-normalized_three_month_drawdown))','acceleration_metric_checks':acceleration['comparisons'],'reference_metric_checks':checks,'winners':verified,'unchanged':[{'id':g['id'],'name':g['name'],'baseline':g['baseline'],'reason':'No new candidate exceeded both current net returns and minimum trade counts'} for g in groups if g['position'] not in top],'note':'Historical selection on reused data, not independent future validation. Positive-return and sample gates applied before scoring.'}
 (FOLDER/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 with s.db.connect() as c,sqlite3.connect(FOLDER/'before-publish.sqlite3') as backup:c.backup(backup)
 for winner in verified:
  key=winner['source'];base=winner['original_name'].split(' · ')[0];name=f"{base} · 新规百万组双窗口提升{winner['score']:.2f}（待验证）";value=winner['settings']
  save=follow_strategy.save if value.get('strategy_type') in ('majority','hourly') else custom_strategy.save
  save(s,{'id':key,'name':name,'settings':value});prepared=hb.prepare(s,{'strategy_id':key});prepared.update(cutoff_id=manifest['coverage']['last_id'],as_of=manifest['as_of']);final=hb.execute(s,prepared)
  assert final['net']==winner['reference']['three']['net'];assert final['window_returns']['one_month']['net']==winner['reference']['one']['net']
  winner.update(name=name,backtest_run=final['run_id'],published=True);(FOLDER/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 after={r['id']:r for r in catalog(s.db,s.cfg)['strategies']}
 for winner in verified:
  row=after[winner['source']];assert row['backtest']['run_id']==winner['backtest_run'];assert not row['backtest']['parameters_changed'];assert row['settings']==winner['settings']
 for row in before:
  if row['id'] not in {w['source'] for w in verified}:
   for field in ('settings','name','enabled','backtest'):assert row[field]==after[row['id']][field]
 restore_runtime();atexit.unregister(restore_runtime);report['runtime_transition']=transition;report['runtime_restored']=transition.get('restored',False)
 report['catalog_verified']=True;(FOLDER/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps({'published':len(verified),'unchanged':len(report['unchanged']),'rows':[{'name':w['name'],'one_before':w['baseline']['one']['net'],'one_after':w['reference']['one']['net'],'three_before':w['baseline']['three']['net'],'three_after':w['reference']['three']['net']} for w in verified]},ensure_ascii=False),flush=True)

if __name__=='__main__':publish()
