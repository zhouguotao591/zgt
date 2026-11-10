import copy,unittest
from pc28.custom_strategy import save
from pc28.follow_strategy import defaults
from pc28.trend_strategy import settings
from pc28.strategies import catalog
import test_custom_strategy as fixture


class StrategyCapitalTests(unittest.TestCase):
 setUp=fixture.CustomStrategyTests.setUp
 tearDown=fixture.CustomStrategyTests.tearDown
 add=fixture.CustomStrategyTests.add

 def assert_capital(self,key,expected):
  if key=='legacy':sql,args='SELECT initial_balance,balance,peak FROM accounts WHERE id=1',()
  elif key in ('size','parity'):sql,args='SELECT initial_balance,balance,peak FROM trend_accounts WHERE strategy=?',(key,)
  else:sql,args='SELECT initial_balance,balance,peak FROM custom_strategies WHERE id=?',(key,)
  self.assertEqual(tuple(self.s.db.query(sql,args)[0].values()),(expected*100,)*3)
  row=next(r for r in catalog(self.s.db,self.s.cfg)['strategies'] if r['id']==key)
  self.assertEqual(row['stats']['balance'],expected)

 def test_save_syncs_unused_accounts_for_all_strategy_types(self):
  rule=copy.deepcopy(self.settings);rule['initial_balance']=1000
  save(self.s,{'id':'legacy','name':'legacy','settings':rule});self.assert_capital('legacy',1000)
  for axis in ('size','parity'):
   rule=settings(self.s.cfg,axis);rule['initial_balance']=1000
   save(self.s,{'id':axis,'settings':rule});self.assert_capital(axis,1000)
  for kind in ('reversal','hourly','majority','previous'):
   rule=copy.deepcopy(self.settings) if kind=='reversal' else defaults(self.s.cfg,kind)
   key=save(self.s,{'name':kind,'settings':rule})['id']
   self.assert_capital(key,100000 if kind=='reversal' else 1000)
   rule['initial_balance']=2500
   save(self.s,{'id':key,'name':kind,'settings':rule});self.assert_capital(key,2500)
   rule['initial_balance']=1000
   save(self.s,{'id':key,'name':kind,'settings':rule});self.assert_capital(key,1000)

 def test_capital_delta_preserves_balance_history_and_is_idempotent(self):
  from pc28.strategy_capital import sync_capital
  with self.s.db.tx() as c:
   c.execute("INSERT INTO ledger(created,kind,amount,balance) VALUES('test','adjust',100,10000100)")
   c.execute("UPDATE accounts SET balance=10000100,peak=10000200 WHERE id=1")
   sync_capital(c,'legacy',200000)
   sync_capital(c,'legacy',200000)
  self.assertEqual(tuple(self.s.db.query('SELECT initial_balance,balance,peak FROM accounts')[0].values()),
                   (20000000,20000100,20000200))
  self.assertEqual(self.s.db.query('SELECT balance FROM ledger')[0]['balance'],10000100)


if __name__=='__main__':unittest.main()
