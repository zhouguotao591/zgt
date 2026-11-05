"""Reproducible chronological parameter search; publish disabled research copies."""
import copy,json,sqlite3,uuid
from pathlib import Path
from types import SimpleNamespace
from threading import RLock
from pc28.db import DB
from pc28.model import utcnow,money
from pc28 import history_backtest as hb

def main():
 root=Path(__file__).resolve().parent
 service=SimpleNamespace(root=root,cfg=json.loads((root/'config.json').read_text('utf8')),db=DB(root/'data/pc28.sqlite3'),lock=RLock())
 keys=['legacy','size','parity']+[r['id'] for r in service.db.query('SELECT id FROM custom_strategies')]
 raw=service.db.query('SELECT * FROM draws ORDER BY id')
 conflicts={r['draw_id'] for r in service.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')}
 rows,coverage=hb.source_window(raw,conflicts,utcnow())
 split=hb.three_months_before(rows[-1]['_time']) # chronological 2/3 split by elapsed time
 boundary=rows[0]['_time']+(rows[-1]['_time']-rows[0]['_time'])*2/3
 train=[r for r in rows if r['_time']<boundary];test=[r for r in rows if r['_time']>=boundary]
 stamp=utcnow().replace(':','').replace('+','_');folder=root/'data/exports';folder.mkdir(exist_ok=True)
 with service.db.connect() as source,sqlite3.connect(folder/f'before-tuning-{stamp}.sqlite3') as backup:source.backup(backup)
 report={'coverage':coverage,'validation_start':boundary.isoformat(),'method':'Train first two thirds; select once; validate final third. No stake or risk increases. Disabled copies only.','strategies':[]}
 def run(part,snap):
  result,_=hb.replay(part,snap)
  return {k:v for k,v in result.items() if k!='curve'}
 for key in keys:
  snap=hb.selection(service,key);snap['maintenance_guard']=service.cfg['collector'].get('maintenance_guard_enabled',True)
  cfg=snap['settings'];kind=snap['kind'];candidates=[copy.deepcopy(snap)]
  changes=({'lookback':[10,20,30,40],'threshold':[4,8,12],'max_bets':[3,5,8],'multiplier_hundredths':[100,150]} if kind=='reversal' else {'lookback':[10,30,40],'win_limit':[0,3,6],'loss_increment_cents':[0,5000]} if kind=='majority' else {'observe_hours':[1,3,4],'hour_difference':[3,7,10],'follow_hours':[1,3]})
  for field,values in changes.items():
   for value in values:
    candidate=copy.deepcopy(snap);target=candidate['settings']['fixed'] if kind=='reversal' else candidate['settings'];target[field]=value;candidates.append(candidate)
  scored=[(run(train,s),s) for s in candidates];base_train=scored[0][0]
  eligible=[p for p in scored if p[0]['bets']>=100 and p[0]['net']>=base_train['net'] and (p[0]['profitable_win_rate'] or 0)>=(base_train['profitable_win_rate'] or 0) and p[0]['max_drawdown']<=base_train['max_drawdown']]
  best,chosen=max(eligible,key=lambda p:(p[0]['net'],p[0]['roi'] or 0)) if eligible else scored[0]
  baseline=run(test,snap);validation=run(test,chosen)
  accepted=chosen['settings']!=cfg and validation['bets']>=100 and validation['net']>0 and validation['net']>baseline['net'] and (validation['profitable_win_rate'] or 0)>=(baseline['profitable_win_rate'] or 0) and validation['max_drawdown']<=baseline['max_drawdown']
  final=chosen if accepted else snap
  settings=copy.deepcopy(final['settings'])
  if kind=='majority':settings['strategy_type']='majority'
  name=snap['name']+(' · 调优验证通过' if accepted else ' · 调优未通过保留原参')
  newkey='custom_'+uuid.uuid4().hex;initial=money(settings['initial_balance'])
  with service.db.tx() as c:
   c.execute('INSERT INTO custom_strategies(id,name,enabled,settings,balance,initial_balance,peak,created) VALUES(?,?,0,?,?,?,?,?)',(newkey,name,json.dumps(settings,ensure_ascii=False),initial,initial,initial,utcnow()))
   c.execute('INSERT INTO strategy_edits(strategy,created,settings) VALUES(?,?,?)',(newkey,utcnow(),json.dumps({'source':key,'accepted':accepted,'settings':settings},ensure_ascii=False)))
  prepared=hb.prepare(service,{'strategy_id':newkey});result=hb.execute(service,prepared)
  entry={'source':key,'name':snap['name'],'candidates':len(candidates),'accepted':accepted,'original_settings':cfg,'candidate_settings':chosen['settings'],'train_baseline':base_train,'train_candidate':best,'validation_baseline':baseline,'validation_candidate':validation,'list_id':newkey,'full_backtest_run':result['run_id'],'full_net':result['net']}
  report['strategies'].append(entry)
  print(json.dumps({'name':snap['name'],'accepted':accepted,'validation_before':baseline['net'],'validation_after':validation['net'],'full_net':result['net']},ensure_ascii=False),flush=True)
  (folder/'strategy-tuning-latest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')

if __name__=='__main__':main()
