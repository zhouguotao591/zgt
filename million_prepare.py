"""Frozen input and canonical exclusions for bounded-memory million-combination search."""
import json,hashlib,sqlite3,copy
from pathlib import Path
from search_parameters import feature,service,ROOT
from pc28 import history_backtest as hb,custom_strategy,follow_strategy
from pc28.strategies import catalog
from pc28.model import utcnow
FOLDER=ROOT/'data/parameter-search-million'

def array_items(path):
 decoder=json.JSONDecoder();buffer='';position=0;started=False;ended=False
 with path.open(encoding='utf8') as stream:
  while not ended:
   chunk=stream.read(1024*1024)
   if not chunk and not buffer[position:].strip():break
   buffer=buffer[position:]+chunk;position=0
   while position<len(buffer):
    while position<len(buffer) and buffer[position] in ' \r\n\t,':position+=1
    if position==len(buffer):break
    if not started:
     assert buffer[position]=='[';position+=1;started=True;continue
    if buffer[position]==']':ended=True;break
    try:item,end=decoder.raw_decode(buffer,position)
    except json.JSONDecodeError:
     if not chunk:raise
     break
    yield item;position=end

def expand(base,kind,t):
 value=copy.deepcopy(base)
 if kind=='reversal':
  threshold,bets,losses,multiplier,limit,pause=t
  value['fixed'].update(threshold=threshold,max_bets=bets,max_losses=losses,multiplier_hundredths=multiplier)
 elif kind=='majority':
  lookback,losses,increment,limit,pause=t;value.update(lookback=lookback,max_losses=losses,loss_increment_cents=increment)
 else:
  observe,difference,follow,limit,pause=t;value.update(observe_hours=observe,hour_difference=difference,follow_hours=follow)
 value.update(win_limit=limit,pause_draws=pause);return value

def prepare():
 FOLDER.mkdir(exist_ok=True);s=service();as_of=utcnow();listed=catalog(s.db,s.cfg)['strategies'];groups=[]
 with s.db.connect() as c,sqlite3.connect(FOLDER/'source.sqlite3') as backup:c.backup(backup)
 raw=s.db.query('SELECT * FROM draws ORDER BY id');conflicts={r['draw_id'] for r in s.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')}
 rows,coverage=hb.source_window(raw,conflicts,as_of);month,monthcoverage=hb.source_window(raw,conflicts,as_of,months=1)
 for pos,row in enumerate(listed):
  snap=hb.selection(s,row['id']);value=snap['settings'];kind=snap['kind']
  if kind=='reversal':
   allowed=('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed','win_limit','pause_draws');value=custom_strategy.validate_settings(s.cfg,{k:v for k,v in value.items() if k in allowed});assert value['mode']=='fixed'
  else:value=follow_strategy.validate(s.cfg,{**follow_strategy.defaults(s.cfg,kind,value['axis']),**value})
  snap['settings']=value;snap['maintenance_guard']=s.cfg['collector'].get('maintenance_guard_enabled',True);baseline={}
  for label,part in [('one',month),('three',rows)]:
   result,_=hb.replay(part,snap);baseline[label]={k:result[k] for k in ('net','bets','wins','refunds','investment','roi','max_drawdown')}
  groups.append({'position':pos,'id':row['id'],'name':row['name'],'enabled':row['enabled'],'settings_before':row['settings'],'base':value,'kind':kind,'snapshot':snap,'baseline':baseline,'seed':202611060000+pos})
  print(json.dumps({'baseline':row['name'],'one':baseline['one']['net'],'three':baseline['three']['net']},ensure_ascii=False),flush=True)
 exclusions={g['id']:set() for g in groups};history=[]
 for path in sorted((ROOT/'data').glob('parameter-search*/plans.json')):
  if path.parent==FOLDER:continue
  count=0
  for item in array_items(path):
   if item['source'] in exclusions:exclusions[item['source']].add(item['parameter_sha256']);count+=1
  history.append({'file':str(path),'records':count});print(json.dumps({'excluded_file':str(path),'records':count}),flush=True)
 for g in groups:
  key=hashlib.sha256(json.dumps(g['base'],sort_keys=True,separators=(',',':')).encode()).hexdigest();exclusions[g['id']].add(key)
  (FOLDER/f"exclude-{g['position']}.txt").write_text('\n'.join(sorted(exclusions[g['id']])),encoding='ascii');g['excluded_count']=len(exclusions[g['id']])
 data={'odds':s.cfg['odds'],'three':feature(rows),'one':feature(month)}
 if not s.cfg['collector'].get('maintenance_guard_enabled',True):
  for name in ('one','three'):data[name]['rest']=[0]*len(data[name]['ids'])
 (FOLDER/'input.json').write_text(json.dumps(data,separators=(',',':')),encoding='utf8')
 manifest={'as_of':as_of,'coverage':coverage,'one_month_coverage':monthcoverage,'strategies':groups,'per_strategy':1000000,'count':1000000*len(groups),'previous_searches':history,'eligibility':{'one_month_min_bets':100,'three_month_min_bets':300,'one_month_net_positive':True,'three_month_net_positive':True,'strict_improvement_in_both_windows':True},'weights':{'one_month_net':.4,'three_month_net':.3,'three_month_max_drawdown':.3},'normalization':'Min-max over candidates satisfying both-window improvement and sample gates; reverse drawdown; equal range gives 0.5.','parameter_bounds':{'win_limit':[0,30],'pause_draws':[1,1000],'reversal':{'threshold':'1..fixed lookback','max_bets':[1,80],'max_losses':[1,3],'multiplier_hundredths':[100,200]},'majority':{'lookback':[2,500],'loss_increment_cents':'0..10000 step 1000','max_losses':[1,3]},'hourly':{'observe_hours':[1,8],'hour_difference':[1,12],'follow_hours':[1,24]}},'result_schema':['group_position','candidate_index','parameter_sha256','compact_parameters','one_month_metrics','three_month_metrics'],'metric_schema':['net','bets','wins','refunds','investment','roi','max_drawdown'],'risk':'Bankroll, base stake, and risk limits preserved. Each window independently restarts bankroll and warmup.'}
 (FOLDER/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
 print(json.dumps({'ready':len(groups),'total':manifest['count']},ensure_ascii=False),flush=True)

if __name__=='__main__':prepare()
