import unittest,json
import test_auth as fixture
from test_admin_permissions import PermissionTests
class TrendHTTPTests(unittest.TestCase):
 setup_username='admin'
 setUp=fixture.AuthTests.setUp
 tearDown=fixture.AuthTests.tearDown
 setup=fixture.AuthTests.setup
 create=PermissionTests.create
 grant=PermissionTests.grant
 login=PermissionTests.login
 def seed(self):
  self.setup();member,data=fixture.AuthTests.member(self,fixture.AuthTests.invite(self)['code'],'trendmember');uid=data['user']['id']
  with self.s.db.tx() as c:
   aid=c.execute("INSERT INTO robot_accounts(user_id,platform_id,external_account_id,name,created,updated) VALUES(?,1,123,'account','t','t')",(uid,)).lastrowid
   bid=c.execute("INSERT INTO robot_bots(account_id,external_bot_key,name,signal_type,created,updated) VALUES(?,123,'bot','TEST','t','t')",(aid,)).lastrowid
   sid=c.execute("INSERT INTO robot_subscriptions(account_id,strategy_id,bot_id,amount_mode,starts) VALUES(?,'size',?,'fixed','{}')",(aid,bid)).lastrowid
  return member,uid,sid
 def test_page_assets_permission_actions_and_member_scope(self):
  from pc28.trend_risk import DEFAULT
  member,uid,sid=self.seed();status,html=self.admin.call('/admin');self.assertEqual(status,200);self.assertIn('策略趋势风控',html);self.assertIn('/admin/assets/trend-risk.js?v=',html)
  self.assertEqual(self.admin.call('/admin/assets/trend-risk.js')[0],200);self.assertEqual(self.admin.call('/admin/assets/trend-risk.css')[0],200)
  self.assertEqual(member.call('/api/admin/trend-risk')[0],403)
  operator=self.create();self.grant(operator,['page.admin-trend-risk']);client=self.login()
  self.assertEqual(client.call('/api/admin/trend-risk')[1]['total'],0)
  with self.s.db.tx() as c:c.execute('INSERT INTO web_member_access VALUES(?,?)',(operator,uid))
  self.assertEqual(client.call('/api/admin/trend-risk')[1]['total'],1)
  body={'id':sid,'version':0,'settings':DEFAULT};self.assertEqual(client.call('/api/admin/trend-risk-save',body)[0],403)
  self.grant(operator,['page.admin-trend-risk','trend_risk.save']);client=self.login()
  self.assertEqual(client.call('/api/admin/trend-risk-save',body)[0],200)
  self.assertEqual(client.call('/api/admin/trend-risk-save',{'id':sid,'version':1,'action':'evaluate'})[0],403)
  self.assertEqual(client.call('/api/admin/trend-risk-save',{'id':sid,'version':1,'action':'pause'})[0],403)
  self.assertEqual(client.call('/api/admin/trend-risk-save',body)[0],409)
  self.assertEqual(client.call('/api/admin/trend-risk-logs?id='+str(sid))[0],200)
  with self.s.db.tx() as c:c.execute('DELETE FROM web_member_access WHERE admin_id=?',(operator,))
  self.assertEqual(client.call('/api/admin/trend-risk-logs?id='+str(sid))[0],403)
if __name__=='__main__':unittest.main()
