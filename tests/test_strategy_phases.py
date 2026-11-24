import unittest,json,copy,tempfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from pc28.strategy_phases import classify,listing
from pc28.trend_risk import NEW_DEFAULT
from pc28.service import Service
from pc28.config import DEFAULT

class PhaseTests(unittest.TestCase):
 def bars(self,n=35,down=False):
  t=datetime(2026,1,1,tzinfo=timezone.utc);values=[]
  for i in range(n):
   v=(-1 if down else 1)*i*10
   values.append(dict(time=(t+timedelta(hours=i)).isoformat(),open=v,high=v+12,low=v-2,close=v+10,orders=5))
  return values
 def test_up_down_and_insufficient(self):
  bars=self.bars();cutoff=datetime.fromisoformat(bars[-1]['time']).timestamp()+3600
  self.assertEqual(classify(bars,NEW_DEFAULT,cutoff)[0],'up')
  self.assertEqual(classify(self.bars(down=True),NEW_DEFAULT,cutoff)[0],'down')
  self.assertEqual(classify(bars[:10],NEW_DEFAULT,cutoff)[0],'unknown')
 def test_unclosed_candle_cannot_change_phase(self):
  bars=self.bars();cutoff=datetime.fromisoformat(bars[-1]['time']).timestamp()+3600
  stage,analysis=classify(bars,NEW_DEFAULT,cutoff)
  future=dict(bars[-1],time=datetime.fromtimestamp(cutoff,timezone.utc).isoformat(),close=-999999,low=-999999)
  self.assertEqual(classify(bars+[future],NEW_DEFAULT,cutoff),(stage,analysis))
 def test_stale_source_is_unknown(self):
  bars=self.bars();last=datetime.fromisoformat(bars[-1]['time'])
  for i in range(1,6):bars.append(dict(time=(last+timedelta(hours=i)).isoformat(),open=350,close=350,low=350,high=350,orders=0))
  cutoff=datetime.fromisoformat(bars[-1]['time']).timestamp()+3600
  self.assertEqual(classify(bars,NEW_DEFAULT,cutoff)[0],'unknown')
 def test_bottom_confirmation_is_later_close_only(self):
  bars=self.bars(2);cutoff=datetime.fromisoformat(bars[-1]['time']).timestamp()+3600
  def analyze_stub(*args):return dict(status='neutral',reason='candidate',last_bar=bars[0],boll={'middle':0},normalized_slope=0)
  def transition_stub(old,result,rule,at):
   result['gate']={'candidate':{'at':at}} if not old else {'phase':'admitted'}
   return dict(action='keep' if not old else 'resume',blocked=1 if not old else 0,gate=result['gate'])
  with patch('pc28.strategy_phases.analyze',side_effect=analyze_stub),patch('pc28.strategy_phases.transition',side_effect=transition_stub):
   self.assertEqual(classify(bars[:1],NEW_DEFAULT,cutoff-3600)[0],'bottom')
   self.assertEqual(classify(bars,NEW_DEFAULT,cutoff)[0],'reversal')
 def test_empty_listing_read_only(self):
  with tempfile.TemporaryDirectory(dir='data') as tmp:
   p=Path(tmp);cfg=copy.deepcopy(DEFAULT);cfg['collector']['enabled']=False;(p/'config.json').write_text(json.dumps(cfg));s=Service(p)
   before=s.db.query('SELECT * FROM custom_strategies');result=listing(s)
   self.assertEqual(len(result['rows']),3);self.assertTrue(all(r['stage']=='unknown' for r in result['rows']));self.assertEqual(before,s.db.query('SELECT * FROM custom_strategies'))
   with self.assertRaises(ValueError):listing(s,'bad')
if __name__=='__main__':unittest.main()
