"""Verify published summaries, parameters and preserved runtime flags."""
import json
from previous_million_continue_prepare import FOLDER,service
from pc28.strategies import catalog
def main():
 s=service();report=json.loads((FOLDER/'report.json').read_text('utf8'));rows={r['id']:r for r in catalog(s.db,s.cfg)['strategies']};verified=[]
 for w in report['winners']:
  r=rows[w['source']];assert r['settings']==w['settings'] and r['backtest']['run_id']==w['backtest_run'] and not r['backtest']['parameters_changed']
  for label,key in [('one','one_month'),('three','three_months')]:
   assert r['backtest'][key]['net']==w['reference'][label]['net']>w['baseline'][label]['net']
  verified.append({'id':r['id'],'name':r['name'],'enabled':r['enabled'],'one_before':w['baseline']['one']['net'],'one_after':w['reference']['one']['net'],'three_before':w['baseline']['three']['net'],'three_after':w['reference']['three']['net'],'three_drawdown':w['reference']['three']['max_drawdown'],'score':w['score'],'settings':r['settings']})
 transition=report['runtime_transition']
 for key,enabled in transition['all_enabled_before'].items():assert rows[key]['enabled']==enabled
 report.update(final_catalog_verified=True,double_window_improvements_verified=True,runtime_flags_verified=True)
 (FOLDER/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');(FOLDER/'catalog-verification.json').write_text(json.dumps(verified,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(verified,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
