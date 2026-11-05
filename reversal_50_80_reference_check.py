import json
from concurrent.futures import ProcessPoolExecutor
from reversal_50_80_prepare import service,FOLDER
from reversal_50_80_reference import initialize,run_reference
from pc28 import history_backtest as hb

def main():
 m=json.loads((FOLDER/'manifest.json').read_text('utf8'));s=service();raw=s.db.query('SELECT * FROM draws WHERE id<=? ORDER BY id',(m['coverage']['last_id'],));conflicts={r['draw_id'] for r in s.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')};rows,c=hb.source_window(raw,conflicts,m['as_of']);month,_=hb.source_window(raw,conflicts,m['as_of'],months=1);assert c['data_sha256']==m['coverage']['data_sha256'];samples=json.loads((FOLDER/'engine-reference-samples.json').read_text('utf8'))
 with ProcessPoolExecutor(max_workers=4,initializer=initialize,initargs=(rows,month,m['strategies'],s.cfg)) as pool:
  for index,_ in enumerate(pool.map(run_reference,samples,chunksize=1),1):
   if index%13==0:print(json.dumps({'official_metric_checks':index*14}),flush=True)
 result={'comparisons':len(samples)*14,'errors':[],'rules':'Original pc28.history_backtest replay, including dynamic round resolution and concrete rearm'};(FOLDER/'official-engine-validation.json').write_text(json.dumps(result,indent=2),encoding='utf8');print(json.dumps(result),flush=True)

if __name__=='__main__':main()
