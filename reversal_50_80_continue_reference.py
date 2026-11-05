"""Independent original-engine reference workers for the two-million search."""
import copy,hashlib,json
from reversal_50_80_continue_prepare import expand
from pc28 import history_backtest as hb,custom_strategy,follow_strategy

def initialize(rows,month,groups,cfg):
 global ROWS,MONTH,GROUPS,CFG
 ROWS,MONTH,GROUPS,CFG=rows,month,groups,cfg
 original=hb.maintenance.block_reason;cache={}
 def calendar(prev,decision,enabled=True,planned=None):
  key=(prev['id'],decision,enabled,planned)
  if key not in cache:cache[key]=original(prev,decision,enabled,planned)
  return cache[key]
 hb.maintenance.block_reason=calendar

def run_reference(row):
 group=GROUPS[row[0]];value=expand(group['base'],group['kind'],row[3])
 if group['kind']=='reversal':assert custom_strategy.validate_settings(CFG,value)==value
 else:assert follow_strategy.validate(CFG,value)==value
 key=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest();assert key==row[2]
 snap=copy.deepcopy(group['snapshot']);snap['settings']=value;detail={}
 for label,part,compact in [('one',MONTH,row[4]),('three',ROWS,row[5])]:
  result,_=hb.replay(part,snap)
  for name,expected in zip(('net','bets','wins','refunds','investment','roi','max_drawdown'),compact):
   actual=result[name];assert (actual is None and expected is None) or actual is not None and expected is not None and abs(actual-expected)<1e-8,(group['id'],row[1],label,name,actual,expected)
  detail[label]={k:v for k,v in result.items() if k!='curve'}
 return value,detail
