"""Verify spawned original-reference workers before publication."""
import hashlib,json
from concurrent.futures import ProcessPoolExecutor
from two_million_prepare import FOLDER,service
from two_million_reference import initialize,run_reference
from pc28 import history_backtest as hb

def main():
 m=json.loads((FOLDER/'manifest.json').read_text('utf8'));s=service();raw=s.db.query('SELECT * FROM draws WHERE id<=? ORDER BY id',(m['coverage']['last_id'],));conflicts={r['draw_id'] for r in s.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')};rows,c=hb.source_window(raw,conflicts,m['as_of']);month,_=hb.source_window(raw,conflicts,m['as_of'],months=1);assert c['data_sha256']==m['coverage']['data_sha256'];tasks=[]
 for kind in ('reversal','majority','hourly'):
  g=next(g for g in m['strategies'] if g['kind']==kind);v=g['base']
  if kind=='reversal':f=v['fixed'];t=[f[k] for k in ('threshold','max_bets','max_losses','multiplier_hundredths')]+[v['win_limit'],v['pause_draws']]
  elif kind=='majority':t=[v[k] for k in ('lookback','max_losses','loss_increment_cents','win_limit','pause_draws')]
  else:t=[v[k] for k in ('observe_hours','hour_difference','follow_hours','win_limit','pause_draws')]
  key=hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest();tasks.append([g['position'],0,key,t,*[[g['baseline'][w][k] for k in ('net','bets','wins','refunds','investment','roi','max_drawdown')] for w in ('one','three')]])
 with ProcessPoolExecutor(max_workers=3,initializer=initialize,initargs=(rows,month,m['strategies'],s.cfg)) as pool:list(pool.map(run_reference,tasks))
 (FOLDER/'reference-worker-smoke.json').write_text(json.dumps({'types':3,'windows':6,'metric_checks':42,'errors':[]},indent=2),encoding='utf8');print('Original-engine process workers: 42 metric checks passed',flush=True)

if __name__=='__main__':main()
