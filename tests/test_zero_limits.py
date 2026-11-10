import unittest,copy,tempfile,json
from pathlib import Path
from unittest.mock import patch
from dataclasses import asdict
from datetime import datetime,timedelta,timezone
from pc28.model import Strategy,SCREENSHOT_ODDS
from pc28.config import DEFAULT,validate
from pc28.trend_strategy import progress,risk_reason,validate_settings,settings
from pc28.follow_strategy import defaults,validate as validate_follow
from pc28.custom_strategy import validate_settings as validate_custom
from pc28.engine import Prepared,replay
from pc28.history_backtest import replay as history_replay

class ZeroLimitTests(unittest.TestCase):
 def test_live_legacy_zero_limits_continue_after_losses(self):
  from pc28.service import Service
  from pc28.router import tick
  cfg=copy.deepcopy(DEFAULT);cfg['collector'].update(enabled=False,maintenance_guard_enabled=False);cfg['simulation'].update(enabled=True,daily_loss_limit=0,max_drawdown=0,max_stake=20000);cfg['simulation']['fixed'].update(direction='大小',lookback=2,threshold=2,run_length=0,max_losses=0,max_bets=10,multiplier_hundredths=100)
  with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as folder:
   root=Path(folder);(root/'config.json').write_text(json.dumps(cfg));service=Service(root);now=datetime.now(timezone.utc).replace(microsecond=0)
   service.db.import_records([{'id':100+i,'time':(now-timedelta(seconds=(39-i)*210)).isoformat(),'numbers':list(range(1,21))} for i in range(40)],'test',received=now.isoformat())
   with patch('pc28.router.utcnow',return_value=now.isoformat()):tick(service.db,cfg,now+timedelta(seconds=1))
   for i in range(1,6):
    t=now+timedelta(seconds=i*210);service.db.import_records([{'id':139+i,'time':t.isoformat(),'numbers':list(range(1,21))}],'test',received=t.isoformat())
    with patch('pc28.router.utcnow',return_value=t.isoformat()):tick(service.db,cfg,t+timedelta(seconds=1))
   state=json.loads(service.db.query('SELECT state FROM accounts WHERE id=1')[0]['state']);self.assertEqual(state['losses'],5);self.assertTrue(state['active']);self.assertEqual(len(service.db.query('SELECT * FROM orders')),6)
 def test_entry_difference_and_optional_opposite_run_are_both_required(self):
  for kind in ('imbalance','reversal'):
   s=Strategy(direction='大小',kind=kind,lookback=6,threshold=2,run_length=3,max_losses=0)
   rows=lambda values:[{'size':v} for v in values]
   self.assertFalse(s.triggered(rows(['大','大','大','小','大','大'])))
   self.assertFalse(s.triggered(rows(['小','小','小','大','大','大'])))
   self.assertEqual(s.resolve(rows(['小','小','大','大','大','大'])).direction,'小')
   unrestricted=Strategy(**{**asdict(s),'run_length':0})
   self.assertTrue(unrestricted.triggered(rows(['大','大','大','小','大','大'])))
   self.assertFalse(unrestricted.triggered(rows(['小','大']*3)))
 def test_zero_limits_validate_for_every_strategy_type(self):
  cfg=copy.deepcopy(DEFAULT);cfg['simulation'].update(daily_loss_limit=0,max_drawdown=0,max_stake=20000);cfg['simulation']['fixed'].update(max_losses=0,run_length=0);validate(cfg);validate_custom(cfg,{k:v for k,v in cfg['simulation'].items() if k in ('mode','initial_balance','daily_loss_limit','max_drawdown','max_stake','round_restart','fixed','win_limit','pause_draws')})
  rule=settings(cfg,'size');rule.update(max_losses=0,daily_loss_limit=0,max_drawdown=0);validate_settings(rule,cfg['odds'])
  for kind in ('hourly','majority','previous'):
   rule=defaults(cfg,kind);rule.update(max_losses=0,daily_loss_limit=0,max_drawdown=0);validate_follow(cfg,rule)
   self.assertEqual(risk_reason(100000,99999999,1000,-99999999,rule,cfg['odds']),'')
   rule['max_drawdown']=-1
   with self.assertRaises(ValueError):validate_follow(cfg,rule)
  for initial in (0,-1):
   cfg['simulation']['initial_balance']=initial
   with self.assertRaises(ValueError):validate(cfg)
 def test_zero_loss_limit_does_not_reset_or_stop_replays(self):
  self.assertEqual(progress(9,False,0),(10,False));self.assertEqual(progress(9,True,0),(0,True))
  now=datetime(2026,1,1,tzinfo=timezone.utc);rows=[]
  for i in range(20):
   t=now+timedelta(seconds=210*i);rows.append({'id':i+1,'source_time':t.isoformat(),'draw_utc':t.isoformat(),'_time':t,'size':'大','parity':'双','total':18})
  s=Strategy(direction='小',lookback=2,threshold=2,max_losses=0,run_length=0,max_bets=10,multiplier_hundredths=100)
  engine=replay(Prepared(rows),s,SCREENSHOT_ODDS,trace=True,initial_cents=10000000,daily_limit=0,dd_limit=0)
  cfg=copy.deepcopy(DEFAULT['simulation']);cfg.update(fixed=asdict(s),daily_loss_limit=0,max_drawdown=0)
  result,orders=history_replay(rows,{'kind':'reversal','settings':cfg,'odds':SCREENSHOT_ODDS,'maintenance_guard':False})
  self.assertEqual(engine['bets'],18);self.assertEqual(engine['loss_stops'],0);self.assertEqual(result['bets'],18);self.assertTrue(orders[9]['round_end']);self.assertFalse(orders[0]['round_end']);self.assertEqual([o['stake']/100 for o in orders],[o['stake'] for o in engine['orders']])
if __name__=='__main__':unittest.main()
