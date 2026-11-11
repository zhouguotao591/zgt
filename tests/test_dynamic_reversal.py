import unittest,tempfile,copy,json
from pathlib import Path
from datetime import datetime,timezone,timedelta
from dataclasses import asdict
from pc28.model import Strategy,SCREENSHOT_ODDS,convert
from pc28.engine import Prepared,replay
from pc28.history_backtest import replay as history_replay
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.custom_strategy import save,tick
from pc28.strategies import control

class DynamicReversalTests(unittest.TestCase):
 def history(self,values,axis='size'):
  return [{axis:v} for v in values]
 def test_both_sides_and_boundary(self):
  for dimension,axis,a,b in [('大小','size','大','小'),('单双','parity','单','双')]:
   s=Strategy(direction=dimension,lookback=6,threshold=2)
   for values,expected in [([a]*4+[b]*2,b),([b]*4+[a]*2,a)]:
    concrete=s.resolve(self.history(values,axis));self.assertEqual(concrete.direction,expected);self.assertTrue(s.triggered(self.history(values,axis)))
   self.assertFalse(s.triggered(self.history([a]*3+[b]*3,axis)));self.assertIsNone(s.resolve(self.history([a]*5,axis)))
 def test_streak_reverse(self):
  s=Strategy(direction='大小',kind='reversal',lookback=6,run_length=3,threshold=2)
  self.assertEqual(s.resolve(self.history(['大']*6)).direction,'小')
  self.assertEqual(s.resolve(self.history(['小']*6)).direction,'大')
  self.assertIsNone(s.resolve(self.history(['小','大']*3)))
 def test_live_concrete_order_and_historical_settlement(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as folder:
   root=Path(folder);cfg=copy.deepcopy(DEFAULT);cfg['collector']['enabled']=False;cfg['collector']['maintenance_guard_enabled']=False
   (root/'config.json').write_text(json.dumps(cfg));service=Service(root);now=datetime.now(timezone.utc).replace(microsecond=0)
   for i in range(6):service.db.import_records([{'id':100+i,'time':(now-timedelta(seconds=210*(5-i))).isoformat(),'numbers':list(range(1,21))}],'test')
   settings={k:copy.deepcopy(cfg['simulation'][k]) for k in ('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed')};settings['fixed'].update(direction='大小',lookback=6,threshold=2,max_bets=1)
   key=save(service,{'name':'dynamic','settings':settings})['id'];control(service,{'id':key,'enabled':True});tick(service.db,cfg,now)
   o=service.db.query('SELECT * FROM custom_orders')[0];self.assertEqual(o['direction'],'小');self.assertEqual(json.loads(o['strategy_json'])['direction'],'小')
   with service.db.tx() as c:c.execute('UPDATE custom_strategies SET enabled=0 WHERE id=?',(key,))
   service.db.import_records([{'id':106,'time':(now+timedelta(seconds=210)).isoformat(),'numbers':list(range(4,24))}],'test');tick(service.db,cfg,now+timedelta(seconds=210))
   self.assertEqual(service.db.query('SELECT status FROM custom_orders')[0]['status'],'settled')
 def test_backtest_engines_agree_and_switch_between_rounds(self):
  start=datetime(2026,10,1,0,1,tzinfo=timezone.utc);rows=[]
  for i,side in enumerate(['大']*10+['小']*10+['大']*10):
   t=start+timedelta(seconds=210*i);total=21 if side=='大' else 13
   rows.append({'id':100+i,'source_time':t.isoformat(),'draw_utc':t.isoformat(),'_time':t,'first_seen':t.isoformat(),'size':side,'parity':'单','total':total})
  s=Strategy(direction='大小',lookback=6,threshold=2,max_bets=1,max_losses=1)
  a=replay(Prepared(rows),s,SCREENSHOT_ODDS,trace=True)
  snapshot={'kind':'reversal','settings':{'mode':'fixed','fixed':asdict(s),'initial_balance':100000,'daily_loss_limit':100000,'max_drawdown':100000,'max_stake':20000,'round_restart':'immediate'},'odds':SCREENSHOT_ODDS,'maintenance_guard':False}
  b,orders=history_replay(rows,snapshot)
  self.assertEqual(a['net'],b['net']);self.assertEqual([(o['draw'],o['direction']) for o in a['orders']],[(o['target_draw'],o['direction']) for o in orders]);self.assertEqual({o['direction'] for o in orders},{'大','小'})


 def test_reversal_enable_gate_accepts_comparison_axes_only_for_reversal(self):
  from pc28.strategies import require_fixed_direction
  for direction,axis in [('大小','size'),('单双','parity')]:
   self.assertEqual(require_fixed_direction({'fixed':{'direction':direction}}),axis)
   with self.assertRaises(ValueError):require_fixed_direction({'strategy_type':'majority','direction':direction})
 def test_editor_exposes_two_comparison_axes(self):
  import re
  html=(Path(__file__).resolve().parent.parent/'web/index.html').read_text('utf-8')
  select=re.search(r'<select id="se-direction"[^>]*>(.*?)</select>',html).group(1)
  self.assertEqual(re.findall(r'value="([^"]+)"',select),['大小','单双'])
