"""Prepare frozen sources and 10,000 unique valid combinations per active catalog row."""
import json,random,copy,hashlib,sqlite3,subprocess
from pathlib import Path
from datetime import timedelta,timezone
from types import SimpleNamespace
from threading import RLock
from pc28 import history_backtest as hb,custom_strategy,follow_strategy
from pc28.db import DB
from pc28.model import utcnow

ROOT=Path(__file__).resolve().parent
FOLDER=ROOT/'data/parameter-search'
def service():
 s=SimpleNamespace(root=ROOT,cfg=json.loads((ROOT/'config.json').read_text('utf8')),db=DB(ROOT/'data/pc28.sqlite3'),lock=RLock());hb.initialize(s.db);return s
def feature(rows):
 n=len(rows);w={k:[] for k in ['ids','days','totals','times','rest','boundary','hourStart','hourIndex','size','parity','gaps']};hourlist=[];first={};gap=0
 for i,r in enumerate(rows):
  t=r['_time'];decision=t-timedelta(microseconds=1);hour=decision.replace(minute=0,second=0,microsecond=0);epoch=int(hour.timestamp())
  if epoch not in first:first[epoch]=i;hourlist.append(epoch)
  if i and r['id']!=rows[i-1]['id']+1:gap+=1
  for k,v in {'ids':r['id'],'days':t.astimezone(timezone.utc).date().toordinal(),'totals':r['total'],'times':t.timestamp(),'rest':int(i>0 and bool(hb.maintenance.block_reason(rows[i-1],decision,True,t))),'boundary':int(t.replace(minute=0,second=0,microsecond=0)>hour),'hourStart':epoch,'hourIndex':len(hourlist)-1,'size':1 if r['size']=='大' else -1,'parity':1 if r['parity']=='单' else -1,'gaps':gap}.items():w[k].append(v)
 for axis in ('size','parity'):
  prefix=[0];runs=[];run=0;last=0
  for v in w[axis]:prefix.append(prefix[-1]+v);run=run+1 if last==v else 1;last=v;runs.append(run*v)
  w[axis+'Prefix']=prefix;w[axis+'Run']=runs
 w['hours']={}
 for axis in ('size','parity'):
  for observe in range(1,9):
   arrays={d:[] for d in range(1,13)}
   for epoch in hourlist:
    i=first[epoch]
    while i<n and w['hourStart'][i]==epoch and w['boundary'][i]:i+=1
    if i==n or w['hourStart'][i]!=epoch:
     for a in arrays.values():a.append(0)
     continue
    decision=rows[i]['_time']-timedelta(microseconds=1);hour=decision.astimezone(hb.BJ).replace(minute=0,second=0,microsecond=0)
    direction,evidence,_=hb.hourly_signal(rows[max(0,i-1001):i],hour,{'axis':axis,'observe_hours':observe,'hour_difference':0},decision)
    minimum=min((r['difference'] for r in evidence),default=-1)
    for d,a in arrays.items():a.append((1 if direction in ('大','单') else -1) if direction and minimum>=d else 0)
   for d,a in arrays.items():w['hours'][f'{axis}_{observe}_{d}']=a
 return w

def prepare():
 FOLDER.mkdir(exist_ok=True);s=service();now=utcnow()
 with s.db.connect() as c,sqlite3.connect(FOLDER/'source.sqlite3') as b:c.backup(b)
 raw=s.db.query('SELECT * FROM draws ORDER BY id');conflicts={r['draw_id'] for r in s.db.query('SELECT draw_id FROM conflicts WHERE resolved=0')}
 rows,coverage=hb.source_window(raw,conflicts,now);month,monthcoverage=hb.source_window(raw,conflicts,now,months=1)
 ids=[r['id'] for r in s.db.query('SELECT id FROM custom_strategies WHERE id NOT IN (SELECT id FROM deleted_strategies) ORDER BY created,id')]
 ids=[k for k in ('legacy','size','parity') if not s.db.query('SELECT 1 FROM deleted_strategies WHERE id=?',(k,))]+ids
 snapshots=[hb.selection(s,k) for k in ids]
 inputdata={'odds':s.cfg['odds'],'three':feature(rows),'one':feature(month)}
 (FOLDER/'input.json').write_text(json.dumps(inputdata,separators=(',',':')),encoding='utf8')
 plans=[];manifest=[]
 for pos,snap in enumerate(snapshots):
  kind=snap['kind'];base=copy.deepcopy(snap['settings']);rng=random.Random(202611020000+pos);seen=set();group=[]
  def add(value):
   normalized=copy.deepcopy(value)
   if kind=='reversal':
    allowed=('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed','win_limit','pause_draws');normalized=custom_strategy.validate_settings(s.cfg,{k:v for k,v in value.items() if k in allowed})
   else:normalized=follow_strategy.validate(s.cfg,{**follow_strategy.defaults(s.cfg,kind,value['axis']),**value})
   key=hashlib.sha256(json.dumps(normalized,sort_keys=True,separators=(',',':')).encode()).hexdigest()
   if key in seen:return
   seen.add(key);p={'index':len(plans),'source':snap['id'],'kind':kind,'settings':normalized,'parameter_sha256':key};plans.append(p);group.append(p['index'])
  add(base)
  while len(group)<10000:
   v=copy.deepcopy(base);v.update(win_limit=rng.choice([0,2,3,4,5,6,7,8,10,12]),pause_draws=rng.choice([5,10,15,20,30,40,60,80,100,120,160,200]))
   if kind=='reversal':
    f=v['fixed'];f.update(threshold=rng.randint(1,min(18,f['lookback'])),max_bets=rng.choice([1,2,3,4,5,6,8,10,12,15,20,30]),max_losses=rng.choice([1,2,3]),multiplier_hundredths=rng.choice([100,110,125,140,150,160,175,190,200]));f['max_losses']=min(f['max_losses'],f['max_bets'])
    if f['max_losses']==1:f['multiplier_hundredths']=100
   elif kind=='majority':
    v.update(lookback=rng.randint(2,300),max_losses=rng.choice([1,2,3]),loss_increment_cents=rng.choice([0,2500,5000,7500,10000]))
    if v['loss_increment_cents']==0:v['max_losses']=1
   else:v.update(observe_hours=rng.randint(1,8),hour_difference=rng.randint(1,12),follow_hours=rng.randint(1,12))
   add(v)
  manifest.append({'id':snap['id'],'name':snap['name'],'snapshot':snap,'count':len(group),'indices':group})
  print(json.dumps({'prepared':snap['name'],'unique':len(group)},ensure_ascii=False),flush=True)
 (FOLDER/'plans.json').write_text(json.dumps(plans,ensure_ascii=False,separators=(',',':')),encoding='utf8')
 (FOLDER/'manifest.json').write_text(json.dumps({'as_of':now,'coverage':coverage,'one_month_coverage':monthcoverage,'strategies':manifest,'count':len(plans),'weights':{'one_month_net':0.4,'three_month_net':0.3,'three_month_max_drawdown':0.3},'normalization':'Global min-max across all candidates; drawdown reversed; equal range contributes 0.5. Score=100*(.4*month+.3*quarter+.3*low_drawdown).'},ensure_ascii=False,indent=2),encoding='utf8')
 # Cross-check stratified candidate samples with the unmodified reference replay.
 checks=[]
 for group in manifest:
  indices=[group['indices'][j] for j in [0,1,17,137,1234,4321,9999]]
  for index in indices:
   p=plans[index];snap=copy.deepcopy(group['snapshot']);snap['settings']=p['settings'];snap['maintenance_guard']=s.cfg['collector'].get('maintenance_guard_enabled',True)
   values={}
   for name,part in [('one',month),('three',rows)]:
    r,_=hb.replay(part,snap);values[name]={k:r[k] for k in ('net','bets','wins','refunds','investment','roi','max_drawdown')}
   checks.append({'index':index,**values})
  print(json.dumps({'reference_checked':group['name']},ensure_ascii=False),flush=True)
 (FOLDER/'reference.json').write_text(json.dumps(checks),encoding='utf8')

if __name__=='__main__':prepare()
