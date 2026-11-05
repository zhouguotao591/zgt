"""One million distinct combinations per current dynamically resolved reversal rule."""
import copy,hashlib,json,sqlite3,gzip
from datetime import timedelta,timezone
from million_prepare import ROOT,array_items,service,expand
from pc28 import history_backtest as hb,custom_strategy
from pc28.strategies import catalog
from pc28.model import utcnow
FOLDER=ROOT/'data/parameter-search-reversal-50-80'

def feature(rows,guard):
 w={k:[] for k in ('ids','days','totals','times','rest','boundary','hourStart','hourIndex','size','parity','gaps')};gap=0
 for i,r in enumerate(rows):
  t=r['_time'];decision=t-timedelta(microseconds=1);hour=decision.replace(minute=0,second=0,microsecond=0)
  if i and r['id']!=rows[i-1]['id']+1:gap+=1
  values={'ids':r['id'],'days':t.astimezone(timezone.utc).date().toordinal(),'totals':r['total'],'times':t.timestamp(),'rest':int(i>0 and bool(hb.maintenance.block_reason(rows[i-1],decision,guard,t))),'boundary':0,'hourStart':int(hour.timestamp()),'hourIndex':0,'size':1 if r['size']=='大' else -1,'parity':1 if r['parity']=='单' else -1,'gaps':gap}
  for k,v in values.items():w[k].append(v)
 for axis in ('size','parity'):
  prefix=[0];runs=[];length=0;last=0
  for v in w[axis]:prefix.append(prefix[-1]+v);length=length+1 if v==last else 1;last=v;runs.append(length*v)
  w[axis+'Prefix']=prefix;w[axis+'Run']=runs
 w['hours']={};return w

def prepare():
 FOLDER.mkdir(exist_ok=True);assert not (FOLDER/'manifest.json').exists();s=service();as_of=utcnow();listed=catalog(s.db,s.cfg)['strategies'];selected=[r for r in listed if 'fixed' in r['settings'] and r['name'].startswith(('五十期','八十期'))];assert len(selected)==4
 with s.db.connect() as c,sqlite3.connect(FOLDER/'source.sqlite3') as backup:c.backup(backup)
 raw=s.db.query('SELECT * FROM draws ORDER BY id');conflicts={r['draw_id'] for r in s.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')};rows,coverage=hb.source_window(raw,conflicts,as_of);month,monthcoverage=hb.source_window(raw,conflicts,as_of,months=1);groups=[]
 for position,row in enumerate(selected):
  snap=hb.selection(s,row['id']);assert snap['kind']=='reversal';snap['settings']['fixed'].update(lookback=50 if row['name'].startswith('五十期') else 80,direction='大小' if '大小' in row['name'] else '单双');value=custom_strategy.validate_settings(s.cfg,snap['settings']);snap['settings']=value;snap['maintenance_guard']=s.cfg['collector'].get('maintenance_guard_enabled',True);baseline={}
  for label,part in [('one',month),('three',rows)]:
   result,_=hb.replay(part,snap);baseline[label]={k:result[k] for k in ('net','bets','wins','refunds','investment','roi','max_drawdown')}
  groups.append({'position':position,'id':row['id'],'name':row['name'],'enabled':row['enabled'],'settings_before':row['settings'],'base':value,'kind':'reversal','snapshot':snap,'baseline':baseline,'seed':202611110000+position})
  print(json.dumps({'baseline':row['name'],'direction':value['fixed']['direction'],'one':baseline['one']['net'],'three':baseline['three']['net']},ensure_ascii=False),flush=True)
 exclusions={g['id']:set() for g in groups};history=[]
 # Direction is immutable within earlier generators. A different direction string
 # proves canonical parameter disjointness without rereading tens of millions of rows.
 for manifest_path in sorted((ROOT/'data').glob('parameter-search*/manifest.json')):
  if manifest_path.parent==FOLDER:continue
  previous=json.loads(manifest_path.read_text('utf8'));compatible=[];proof=[]
  for old in previous.get('strategies',[]):
   if old['id'] not in exclusions:continue
   old_base=old.get('base') or old.get('snapshot',{}).get('settings',{})
   current=next(g for g in groups if g['id']==old['id'])
   if old_base.get('fixed',{}).get('direction')==current['base']['fixed']['direction']:compatible.append(old)
   else:proof.append({'source':old['id'],'previous_direction':old_base.get('fixed',{}).get('direction'),'current_direction':current['base']['fixed']['direction']})
  history.append({'manifest':str(manifest_path),'incompatible_direction_proof':proof,'compatible_sources':[g['id'] for g in compatible]})
  if not compatible:continue
  keys={g['id'] for g in compatible};plans=manifest_path.parent/'plans.json'
  if plans.exists():
   for p in array_items(plans):
    if p['source'] in keys:exclusions[p['source']].add(p['parameter_sha256'])
  else:
   for old in compatible:
    with gzip.open(manifest_path.parent/f"results-{old['position']}.ndjson.gz",'rt',encoding='utf8') as stream:
     for line in stream:exclusions[old['id']].add(json.loads(line)[2])
 for g in groups:
  exclusions[g['id']].add(hashlib.sha256(json.dumps(g['base'],sort_keys=True,separators=(',',':')).encode()).hexdigest());g['excluded_count']=len(exclusions[g['id']])
  with (FOLDER/f"exclude-{g['position']}.txt").open('w',encoding='ascii',newline='\n') as out:out.write('\n'.join(sorted(exclusions[g['id']]))+'\n')
 data={'odds':s.cfg['odds'],'one':feature(month,s.cfg['collector'].get('maintenance_guard_enabled',True)),'three':feature(rows,s.cfg['collector'].get('maintenance_guard_enabled',True))};(FOLDER/'input.json').write_text(json.dumps(data,separators=(',',':')),encoding='utf8')
 rule_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ('pc28/model.py','pc28/history_backtest.py')}
 manifest={'as_of':as_of,'coverage':coverage,'one_month_coverage':monthcoverage,'strategies':groups,'per_strategy':1000000,'count':4000000,'rule_source_sha256':rule_hashes,'rules':'Resolve minority at round entry; lock concrete direction for the round; loss-reset rearm uses that concrete direction.','previous_searches':history,'eligibility':{'one_month_min_bets':100,'three_month_min_bets':300,'strict_improvement_in_both_windows':False,'both_net_positive':True},'weights':{'one_month_net':.4,'three_month_net':.3,'three_month_max_drawdown':.3},'result_schema':['group_position','candidate_index','parameter_sha256','compact_parameters','one_month_metrics','three_month_metrics'],'metric_schema':['net','bets','wins','refunds','investment','roi','max_drawdown']}
 (FOLDER/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({'ready':4,'count':4000000}),flush=True)

if __name__=='__main__':prepare()
