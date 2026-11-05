"""Rank all candidates, verify winners with reference engine, update saved definitions."""
import json,csv,copy,sqlite3
from search_parameters import ROOT,FOLDER,service
from pc28 import history_backtest as hb,custom_strategy,follow_strategy
from pc28.model import utcnow

def main():
 manifest=json.loads((FOLDER/'manifest.json').read_text('utf8'));plans=json.loads((FOLDER/'plans.json').read_text('utf8'));results=json.loads((FOLDER/'results.json').read_text('utf8'));validation=json.loads((FOLDER/'validation.json').read_text('utf8'))
 assert not validation['errors']
 assert len(results)==len(plans)==10000*len(manifest['strategies'])
 for group in manifest['strategies']:
  assert len(group['indices'])==10000
  assert len({plans[i]['parameter_sha256'] for i in group['indices']})==10000
 metrics=[('one','net',.4,False),('three','net',.3,False),('three','max_drawdown',.3,True)]
 ranges={f'{w}.{k}':{'min':min(r[w][k] for r in results),'max':max(r[w][k] for r in results)} for w,k,_,_ in metrics}
 for r in results:
  score=0
  for w,k,weight,reverse in metrics:
   bounds=ranges[f'{w}.{k}'];lo,hi=bounds['min'],bounds['max'];component=(r[w][k]-lo)/(hi-lo) if hi!=lo else .5
   score+=weight*(1-component if reverse else component)
  r['score']=100*score
 ordering=lambda r:(r['score'],r['one']['net'],r['three']['net'],-r['three']['max_drawdown'],-r['index'])
 ranked=sorted(results,key=ordering,reverse=True);winners=[max((results[i] for i in g['indices']),key=ordering) for g in manifest['strategies']];global_index=ranked[0]['index']
 s=service()
 # Verify frozen source identity against the live DB before any strategy mutation.
 raw=s.db.query('SELECT * FROM draws WHERE id<=? ORDER BY id',(manifest['coverage']['last_id'],));conflicts={r['draw_id'] for r in s.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')}
 rows,coverage=hb.source_window(raw,conflicts,manifest['as_of']);month,monthcoverage=hb.source_window(raw,conflicts,manifest['as_of'],months=1)
 assert coverage['data_sha256']==manifest['coverage']['data_sha256'],'Historical source changed during search'
 assert monthcoverage['data_sha256']==manifest['one_month_coverage']['data_sha256']
 verified=[]
 for g,r in zip(manifest['strategies'],winners):
  assert hb.selection(s,g['id'])['settings']==g['snapshot']['settings'],'Saved parameters changed during search'
  p=plans[r['index']];snap=copy.deepcopy(g['snapshot']);snap['settings']=p['settings'];snap['maintenance_guard']=s.cfg['collector'].get('maintenance_guard_enabled',True)
  detail={}
  for name,part in [('one',month),('three',rows)]:
   ref,_=hb.replay(part,snap)
   for key in ('net','bets','wins','refunds','investment','roi','max_drawdown'):
    a,b=ref[key],r[name][key]
    assert (a is None and b is None) or a is not None and b is not None and abs(a-b)<1e-8,(g['id'],name,key,a,b)
   detail[name]={k:v for k,v in ref.items() if k!='curve'}
  verified.append({'source':g['id'],'original_name':g['name'],'candidate_index':r['index'],'parameter_sha256':p['parameter_sha256'],'score':r['score'],'is_global_winner':r['index']==global_index,'settings':p['settings'],'reference':detail,'baseline':results[g['indices'][0]]})
  print(json.dumps({'winner_verified':g['name'],'score':r['score'],'month':r['one']['net'],'quarter':r['three']['net']},ensure_ascii=False),flush=True)
 with (FOLDER/'ranking.csv').open('w',encoding='utf-8-sig',newline='') as f:
  writer=csv.DictWriter(f,fieldnames=['rank','source','index','parameter_sha256','score','one_month_net','three_month_net','three_month_max_drawdown','one_month_roi','three_month_roi','one_month_bets','three_month_bets']);writer.writeheader()
  for rank,r in enumerate(ranked,1):
   p=plans[r['index']];writer.writerow({'rank':rank,'source':p['source'],'index':r['index'],'parameter_sha256':p['parameter_sha256'],'score':r['score'],'one_month_net':r['one']['net'],'three_month_net':r['three']['net'],'three_month_max_drawdown':r['three']['max_drawdown'],'one_month_roi':r['one']['roi'],'three_month_roi':r['three']['roi'],'one_month_bets':r['one']['bets'],'three_month_bets':r['three']['bets']})
 report={'created':utcnow(),'manifest':manifest,'normalization_ranges':ranges,'formula':'100*(0.40*minmax(one_month_net)+0.30*minmax(three_month_net)+0.30*(1-minmax(three_month_max_drawdown)))','validation':validation,'winner_reference_checks':len(verified)*14,'winners':verified,'global_winner_index':global_index,'note':'Historical parameter selection on reused data; no claim of independent validation. Original risk budgets/base stake preserved. Each window starts independent bankroll and warmup.'}
 (FOLDER/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 with s.db.connect() as c,sqlite3.connect(FOLDER/'before-publish.sqlite3') as backup:c.backup(backup)
 for winner in verified:
  key=winner['source'];base=winner['original_name'].split(' · ')[0];label='全局评分冠军' if winner['is_global_winner'] else '万组评分最优';name=f"{base} · {label}{winner['score']:.2f}（待验证）"
  settings=winner['settings'];save=follow_strategy.save if settings.get('strategy_type') in ('majority','hourly') else custom_strategy.save
  save(s,{'id':key,'name':name,'settings':settings})
  prepared=hb.prepare(s,{'strategy_id':key});prepared['cutoff_id']=manifest['coverage']['last_id'];prepared['as_of']=manifest['as_of']
  final=hb.execute(s,prepared)
  assert abs(final['net']-winner['reference']['three']['net'])<1e-8
  assert abs(final['window_returns']['one_month']['net']-winner['reference']['one']['net'])<1e-8
  assert hb.selection(s,key)['settings']==settings
  winner.update(name=name,backtest_run=final['run_id'],published=True)
  (FOLDER/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps({'published':len(verified),'global_winner':next(w for w in verified if w['is_global_winner'])},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
