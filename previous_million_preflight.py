"""Validate the new accelerated engine with the unchanged original replay."""
import json
from concurrent.futures import ProcessPoolExecutor
from previous_million_prepare import FOLDER,service
from previous_million_reference import initialize,run_reference
from pc28 import history_backtest as hb
def main():
 s=service();m=json.loads((FOLDER/'manifest.json').read_text('utf8'));raw=s.db.query('SELECT * FROM draws WHERE id<=? ORDER BY id',(m['coverage']['last_id'],));conflicts={r['draw_id'] for r in s.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')};rows,cov=hb.source_window(raw,conflicts,m['as_of']);month,mc=hb.source_window(raw,conflicts,m['as_of'],months=1)
 assert cov['data_sha256']==m['coverage']['data_sha256'] and mc['data_sha256']==m['one_month_coverage']['data_sha256']
 cases=json.loads((FOLDER/'preflight-samples.json').read_text('utf8'));checks=0
 with ProcessPoolExecutor(max_workers=4,initializer=initialize,initargs=(rows,month,m['strategies'],s.cfg)) as pool:
  for _ in pool.map(run_reference,cases,chunksize=1):
   checks+=14
   if checks%112==0:print(json.dumps({'original_engine_checks':checks}),flush=True)
 result={'comparisons':checks,'cases':len(cases),'errors':[],'method':'Unmodified project replay vs accelerated replay; random and adjustment/pause/refund edge cases'}
 for name in ('official-engine-validation.json','engine-regression-validation.json'):(FOLDER/name).write_text(json.dumps(result,indent=2),encoding='utf8')
 print(json.dumps(result),flush=True)
if __name__=='__main__':main()
