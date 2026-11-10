import unittest
import test_auth as fixture
from pc28.model import utcnow

class OverviewMetricTests(unittest.TestCase):
 setup_username='admin'
 setUp=fixture.AuthTests.setUp
 tearDown=fixture.AuthTests.tearDown
 setup=fixture.AuthTests.setup
 def test_member_counts_and_realized_profit_exclude_admins(self):
  self.setup()
  with self.s.db.tx() as c:
   def user(name,role,active):return c.execute('INSERT INTO web_users(username,password_hash,role,active,created) VALUES(?,?,?,?,?)',(name,'fixture',role,active,utcnow())).lastrowid
   a=user('extra_admin','admin',1);user('disabled_admin','admin',0);m=user('active_member','member',1);n=user('disabled_member','member',0)
   for i,(uid,status,pnl) in enumerate([(m,'settled',12345),(m,'settled',-4500),(m,'settled',0),(n,'settled',-1000),(m,'pending',999999),(m,'void',999999),(a,'settled',777777)]):
    c.execute("INSERT INTO member_orders(user_id,outbox_id,target_draw,direction,stake,placed_at,accepted_at,odds_json,status,pnl) VALUES(?,?,?,'大',100,?,?,'{}',?,?)",(uid,i+9000,100+i,utcnow(),utcnow(),status,pnl))
   c.execute('INSERT INTO member_accounts VALUES(?,?,?)',(m,99999999,99999999))
  code,data=self.admin.call('/api/admin/overview');self.assertEqual(code,200);self.assertEqual(data['members'],2);self.assertEqual(data['active_members'],1);self.assertEqual(data['member_net_profit'],6845)
 def test_empty_members_zero_metrics(self):
  self.setup();code,data=self.admin.call('/api/admin/overview');self.assertEqual(code,200);self.assertEqual((data['members'],data['active_members'],data['member_net_profit']),(0,0,0))

if __name__=='__main__':unittest.main()
