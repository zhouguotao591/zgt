import json
import unittest
from datetime import datetime, timedelta, timezone

import test_auth as fixture
from pc28.auth import hash_password
from pc28.member_center import BJ, midnight
from pc28.member_orders import entry


class MemberCenterTests(unittest.TestCase):
    setUp = fixture.AuthTests.setUp
    tearDown = fixture.AuthTests.tearDown
    setup = fixture.AuthTests.setup

    def test_member_login_scope_and_order_reconciliation(self):
        self.setup()
        now = datetime.now(timezone.utc)
        today = now.astimezone(BJ).date()
        boundary = midnight(today)
        before = (now-timedelta(days=100)).isoformat()
        password = 'isolated-member-123'
        with self.s.db.tx() as c:
            users = [c.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES(?,?,'member',?)",
                               (name,hash_password(password),before)).lastrowid for name in ('center_one','center_two')]
            first,second = users
            for uid in users:
                entry(c,uid,'opening',100000,100000,created=before)
            platform = c.execute("INSERT INTO robot_platforms(name,endpoint,created,updated) VALUES('Platform','',?,?)",
                                 (before,before)).lastrowid
            accounts = [c.execute("""INSERT INTO robot_accounts(user_id,platform_id,external_account_id,name,created,updated)
                                     VALUES(?,?,?,?,?,?)""",(uid,platform,uid,'Account '+str(uid),before,before)).lastrowid
                        for uid in users]
            ids=[]
            for i,(uid,account,state,pnl,draw) in enumerate([
                (first,accounts[0],'settled',5000,boundary),
                (first,accounts[0],'settled',-1000,(datetime.fromisoformat(boundary)-timedelta(seconds=1)).isoformat()),
                (first,accounts[0],'pending',None,None),
                (second,accounts[1],'settled',9999,boundary),
            ]):
                ids.append(c.execute("""INSERT INTO member_orders(user_id,outbox_id,target_draw,direction,stake,
                    placed_at,accepted_at,odds_json,status,draw_time,pnl,follow_account_id,total,outcome)
                    VALUES(?,?,?,'大',10000,?,?,?,?,?,?,?,?,?)""",
                    (uid,9000+i,1000+i,before,before,json.dumps(self.s.cfg['odds']),state,draw,pnl,account,18,
                     'win' if pnl and pnl>0 else 'loss' if pnl is not None else None)).lastrowid)
        guest=fixture.Client(self.url,namespace='/member')
        self.assertEqual(guest.call('/api/dashboard')[0],401)
        guest.session()
        self.assertEqual(guest.call('/api/auth/login',{'username':'siteadmin','password':'admin-password-123',
                                                       **guest.captcha()})[0],401)
        credentials={'username':'center_one','password':password}
        self.assertEqual(self.admin.authenticate('login',**credentials)[0],401)
        status,session=guest.call('/api/auth/login',{**credentials,**guest.captcha()})
        self.assertEqual(status,200,session)
        guest.csrf=session['token']
        self.assertEqual(guest.call('/admin/api/admin/users')[0],401)
        status,data=guest.call('/api/dashboard')
        self.assertEqual(status,200,data)
        self.assertEqual((data['total']['net'],data['total']['pending_amount'],data['settled_funds']),(4000,10000,104000))
        self.assertEqual((data['periods'][0]['opening'],data['periods'][0]['net']),(99000,5000))
        status,orders=guest.call('/api/orders?time=draw&start='+today.isoformat()+'&end='+today.isoformat())
        self.assertEqual(status,200,orders)
        self.assertEqual((orders['summary']['total'],orders['summary']['net']),(1,5000))
        status,pending=guest.call('/api/orders?time=draw&status=pending')
        self.assertEqual(status,200,pending)
        self.assertEqual([r['draw_time'] for r in pending['rows']],[None])
        self.assertEqual(guest.call('/api/order?id='+str(ids[0]))[0],200)
        self.assertEqual(guest.call('/api/order?id='+str(ids[-1]))[0],404)
        self.assertEqual(guest.call('/api/orders?accounts='+str(accounts[-1]))[0],404)
        self.assertIn('data-authenticated="1"',guest.call('/member')[1])
        self.assertEqual(guest.call('/api/auth/logout',{})[0],200)
        self.assertEqual(guest.call('/api/dashboard')[0],401)
        self.assertIn('data-authenticated="0"',guest.call('/member')[1])
