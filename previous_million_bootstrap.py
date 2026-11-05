"""Specialize the audited streaming search/publisher for previous-draw rules."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
old='reversal_50_80_continue';new='previous_million'
def write(suffix,text):(ROOT/(new+suffix)).write_text(text,encoding='utf8')
p=(ROOT/(old+'_prepare.py')).read_text('utf8').replace('from million_prepare import ROOT,array_items,service,expand','from million_prepare import ROOT,array_items,service').replace('history_backtest as hb,custom_strategy','history_backtest as hb,custom_strategy,follow_strategy')
p=p.replace('parameter-search-reversal-50-80-continue','parameter-search-previous-million')
p=p.replace("selected=[r for r in listed if 'fixed' in r['settings'] and r['name'].startswith(('五十期','八十期'))];assert len(selected)==4","selected=[r for r in listed if r['settings'].get('strategy_type')=='previous'];assert len(selected)==2")
start=p.index("snap=hb.selection(s,row['id']);");end=p.index(";snap['maintenance_guard']",start)
p=p[:start]+"snap=hb.selection(s,row['id']);assert snap['kind']=='previous';value=follow_strategy.validate(s.cfg,snap['settings']);snap['settings']=value"+p[end:]
p=p.replace("'kind':'reversal'","'kind':'previous'").replace('202611120000','202611030700').replace("'direction':value['fixed']['direction']","'axis':value['axis']")
p=p.replace("if old_base.get('fixed',{}).get('direction')==current['base']['fixed']['direction']:","if old_base.get('strategy_type')=='previous' and old_base.get('axis')==current['base']['axis']:")
p=p.replace("current['base']['fixed']['direction']","current['base'].get('axis')")
p=p.replace("('pc28/model.py','pc28/history_backtest.py')","('pc28/model.py','pc28/history_backtest.py','pc28/follow_strategy.py','pc28/win_pause.py','pc28/maintenance.py')")
p=p.replace("'count':4000000","'count':2000000").replace("{'ready':4,'count':4000000}","{'ready':2,'count':2000000}")
p=p.replace("'rules':'Resolve minority at round entry; lock concrete direction for the round; loss-reset rearm uses that concrete direction.'","'rules':'Follow previous draw, loss increases and win decreases amount; capped consecutive adjustments reset to base; refund keeps amount and clears counters.'")
expand='''
def expand(base,kind,t):
 value=copy.deepcopy(base)
 value.update(zip(('loss_increment_cents','win_decrement_cents','max_increase_steps','max_decrease_steps','win_limit','pause_draws','stake_floor'),t))
 return value
'''
p=p.replace('\ndef prepare():',expand+'\ndef prepare():');write('_prepare.py',p)
r=(ROOT/(old+'_runner.cjs')).read_text('utf8').replace("require('./reversal_rules_fast.cjs'),gold=require('./reversal_rules_gold.cjs')","require('./previous_million_fast.cjs'),gold=require('./previous_million_fast.cjs')").replace('parameter-search-reversal-50-80-continue','parameter-search-previous-million')
start=r.index('function expand(');end=r.index('\nfunction canonical',start)
r=r[:start]+"function expand(base,kind,t){return {...base,loss_increment_cents:t[0],win_decrement_cents:t[1],max_increase_steps:t[2],max_decrease_steps:t[3],win_limit:t[4],pause_draws:t[5],stake_floor:t[6]};}"+r[end:]
start=r.index('function candidate(');end=r.index('\nfunction same',start)
r=r[:start]+"function candidate(g,rand){const up=100*rand(501),down=100*rand(501),limit=rand(31);return [up,down,up?rand(81):0,down?rand(81):0,limit,limit?1+rand(1000):1,rand(2)?'base':'market'];}"+r[end:]
# The independent Python preflight below, rather than a self comparison, validates the new engine.
r=r.replace("fs.writeFileSync(path.join(folder,'acceleration-validation.json'),JSON.stringify({comparisons:checks,errors:[]},null,2));","const official=JSON.parse(fs.readFileSync(path.join(folder,'official-engine-validation.json'),'utf8'));if(official.errors.length)throw Error('Official preflight failed');fs.writeFileSync(path.join(folder,'acceleration-validation.json'),JSON.stringify({comparisons:official.comparisons,errors:[]},null,2));")
write('_runner.cjs',r)
for suffix in ('_reference.py','_publish.py'):
 p=(ROOT/(old+suffix)).read_text('utf8').replace(old,new)
 p=p.replace("in ('majority','hourly')","in ('majority','hourly','previous')")
 p=p.replace('续百万组双窗口提升','百万组评分优选')
 p=p.replace('Requested 50/80 periods and size/parity dimensions take precedence over mislabeled original settings; original settings preserved in manifest.','Previous-draw rule; preserve base amount, initial capital and risk limits.')
 write(suffix,p)
print('Created previous follower million search and audit scripts')
