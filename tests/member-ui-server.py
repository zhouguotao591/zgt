import copy,json,shutil,tempfile
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.server import LocalServer
from pc28 import member_orders as finance
from pc28.auth import hash_password

with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as tmp:
 root=Path(tmp);cfg=copy.deepcopy(DEFAULT);cfg['port']=18032;cfg['collector']['enabled']=False
 (root/'config.json').write_text(json.dumps(cfg),encoding='utf8');shutil.copytree(Path(__file__).resolve().parent.parent/'web',root/'web');s=Service(root)
 now=datetime.now(timezone.utc)
 with s.db.tx() as c:
  c.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES('admin',?,'admin',?)",(hash_password('temporary-test-password-123'),now.isoformat()))
  uid=c.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES('fixture_member','fixture','member',?)",(now.isoformat(),)).lastrowid
  binding=c.execute("INSERT INTO robot_bindings(user_id,account_id,bot_key,signal_type,strategy_ids,starts,created,updated) VALUES(?,1,1,'BUY_SYSTEM','[]','{}',?,?)",(uid,now.isoformat(),now.isoformat())).lastrowid
  finance.entry(c,uid,'opening',100000,100000,note='测试初始化')
 from pc28.permissions import migrate
 migrate(s.auth)
 for i in range(35):
  placed=(now-timedelta(seconds=(36-i)*210)).isoformat();direction=['大','小','单','双'][i%4];target=100+i
  with s.db.tx() as c:
   oid=c.execute("INSERT INTO robot_outbox(event_id,name,engine,strategy_name,target_draw,event_time,created,request,message,status,recipient_id,recipient_name,push_cents,platform_id,member_user_id,member_odds,sent_at,accepted_at,finished) VALUES(?, 'fixture','custom','测试策略',?,0,?,'{}',?,'sent',?,'fixture_member',1000,1,?,?,?,?,?)",('fixture-'+str(i),target,placed,direction+'10',binding,uid,json.dumps(cfg['odds']),placed,placed,placed)).lastrowid
   row=c.execute('SELECT * FROM robot_outbox WHERE id=?',(oid,)).fetchone();finance.import_signal(c,s,row,'fixture-batch-'+str(i),placed,placed)
  s.db.import_records([{'id':target,'time':(datetime.fromisoformat(placed)+timedelta(seconds=100)).isoformat(),'numbers':list(range(1,21))}],'fixture')
 finance.sync(s)
 original=s.auth.captcha
 def captcha(session,ip):
  with patch('pc28.auth.secrets.randbelow',return_value=1):return original(session,ip)
 s.auth.captcha=captcha
 state={'root':str(root),'setup_code':(root/'data/admin-setup-code.txt').read_text(),'control_token':(root/'data/local-control-token.txt').read_text()}
 (Path(__file__).resolve().parent.parent/'data/member-ui-test-state.json').write_text(json.dumps(state),encoding='utf8')
 server=LocalServer(s);print('READY isolated member fixture 18032',flush=True)
 try:server.serve_forever(poll_interval=.1)
 finally:server.server_close()
