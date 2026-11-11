import sys,copy,json,tempfile,shutil,secrets
from pathlib import Path
from datetime import datetime,timezone,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pc28.config import DEFAULT
from pc28.service import Service
from pc28.server import LocalServer,Handler
from pc28.auth import hash_password
from pc28.model import utcnow
with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as folder:
 root=Path(folder);cfg=copy.deepcopy(DEFAULT);cfg['port']=18039;cfg['collector']['enabled']=False;cfg['collector']['maintenance_guard_enabled']=False;(root/'config.json').write_text(json.dumps(cfg));shutil.copytree(Path(__file__).resolve().parent.parent/'web',root/'web');s=Service(root)
 with s.db.tx() as c:uid=c.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES(?,?,?,?)",('hall_test_member',hash_password(secrets.token_urlsafe(24)),'member',utcnow())).lastrowid
 row,cookie=s.auth.create_session(uid)
 with s.db.tx() as c:aid=c.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES(?,?,?,?)",('hall_test_admin',hash_password(secrets.token_urlsafe(24)),'admin',utcnow())).lastrowid
 with s.db.tx() as c:
  c.execute('INSERT OR IGNORE INTO web_admin_owners VALUES(?)',(aid,))
  c.execute('INSERT OR IGNORE INTO web_member_access VALUES(?,?)',(aid,uid))
 with s.db.tx() as c:_,admin_cookie=s.auth.write_session(c,aid,scope='admin',verified=True)
 admin_entry=secrets.token_urlsafe(20);entry=secrets.token_urlsafe(20);now=datetime.now(timezone.utc)-timedelta(seconds=12*210)
 s.db.import_records([{'id':1000+i,'time':(now-timedelta(seconds=(239-i)*210)).isoformat(),'numbers':list(range(1,21)) if i%3 else list(range(4,24))} for i in range(240)],'test')
 from pc28.follow_strategy import defaults
 from pc28.custom_strategy import save,tick
 from pc28.strategies import control
 from unittest.mock import patch
 settings=defaults(cfg,'previous');settings.update(daily_loss_limit=100000,max_drawdown=100000)
 strategy_id=save(s,{'name':'上期大小跟随示例','settings':settings})['id'];control(s,{'id':strategy_id,'enabled':True})
 with s.db.tx() as c:c.execute('UPDATE draws SET first_seen=draw_utc')
 def step(t):
  with patch('pc28.custom_strategy.utcnow',return_value=t.isoformat()),patch('pc28.follow_strategy.utcnow',return_value=t.isoformat()):tick(s.db,cfg,t)
 step(now+timedelta(seconds=1))
 for index,side in enumerate(['小','小','大','大','小','小','小','大','大','小','大','大'],1):
  t=now+timedelta(seconds=index*210);s.db.import_records([{'id':1239+index,'time':t.isoformat(),'numbers':list(range(1,21)) if side=='大' else list(range(4,24))}],'test',received=t.isoformat());step(t+timedelta(seconds=1))
 if '--pagination' in sys.argv:
  for i in range(12):
   key=save(s,{'name':f'分页验证策略 {i+1:02d}','settings':settings})['id'];control(s,{'id':key,'enabled':True})
 if '--strategy-center' in sys.argv:
  from pc28.history_backtest import prepare,execute
  execute(s,prepare(s,{'strategy_id':strategy_id}))
  second=save(s,{'name':'上期单双跟随示例','settings':{**settings,'axis':'parity'}})['id'];control(s,{'id':second,'enabled':True});execute(s,prepare(s,{'strategy_id':second}))
 if '--robot-records' in sys.argv:
  import time
  with s.db.tx() as c:
   for i in range(3):c.execute("INSERT INTO robot_outbox(event_id,name,event_time,created,request,message,status,is_test,recipient_name,target_draw,strategy_name) VALUES(?,?,?,?,?,?,'failed',1,?,?,?)",(f'fixture-record-{i}','测试消息',int(time.time()),utcnow(),'{}',f'大10 双{20+i}','hall_test_member',3500000+i,'测试策略'))
 if '--ai' in sys.argv:
  import sqlite3
  source=sqlite3.connect((Path(__file__).resolve().parent.parent/'data/pc28.sqlite3').as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
  source_rows=list(reversed([dict(r) for r in source.execute('SELECT * FROM draws ORDER BY id DESC LIMIT 401')]));model=source.execute('SELECT * FROM ai_models ORDER BY created DESC,rowid DESC LIMIT 1').fetchone();source.close()
  with s.db.tx() as c:c.execute('DELETE FROM draws')
  s.db.import_records([{'id':r['id'],'time':r['draw_utc'],'numbers':json.loads(r['numbers'])} for r in source_rows],'test',received=utcnow())
  if model:
   with s.db.tx() as c:c.execute('INSERT INTO ai_models VALUES(?,?,?,?,?,?)',tuple(model))
  s.ai.tick()
 if '--history-result' in sys.argv:
  import sqlite3
  source=sqlite3.connect((Path(__file__).resolve().parent.parent/'data/pc28.sqlite3').as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
  saved=source.execute('SELECT * FROM history_backtest_runs ORDER BY created DESC,rowid DESC LIMIT 1').fetchone();value=json.loads(saved['result']);coverage=value['coverage']
  draws=source.execute('SELECT * FROM draws WHERE id BETWEEN ? AND ? ORDER BY id',(coverage['first_id'],coverage['last_id'])).fetchall();orders=source.execute('SELECT * FROM history_backtest_orders WHERE run_id=?',(saved['id'],)).fetchall()
  with s.db.tx() as c:
   c.execute('DELETE FROM draws');columns=','.join(draws[0].keys());c.executemany('INSERT INTO draws('+columns+') VALUES('+','.join('?' for _ in draws[0])+')',[tuple(r) for r in draws])
   c.execute('INSERT INTO history_backtest_runs VALUES(?,?,?)',tuple(saved));c.executemany('INSERT INTO history_backtest_orders VALUES(?,?,?)',[tuple(r) for r in orders])
  source.close()
 class FixtureHandler(Handler):
  def check_host(self):return super().check_host() or self.headers.get('Host')=='pc28-next.localhost:18039'
  def do_POST(self):
   if self.headers.get('Origin')=='http://pc28-next.localhost:18039':self.headers.replace_header('Origin','http://127.0.0.1:18039')
   super().do_POST()
  def do_GET(self):
   if self.path=='/fixture-entry/'+entry and self.check_host():
    self.send_response(303);self.send_header('Set-Cookie',cookie);self.send_header('Location','/');self.end_headers();return
   if self.path=='/fixture-admin-entry/'+admin_entry and self.check_host():
    self.send_response(303);self.send_header('Set-Cookie',admin_cookie);self.send_header('Location','/admin');self.end_headers();return
   super().do_GET()
 if '--library-backtests' in sys.argv:
  from pc28.algorithm_library import run as library_backtests
  library_backtests(root)
 server=LocalServer(s);server.RequestHandlerClass=FixtureHandler;s.start()
 state={'root':str(root),'admin_entry':'http://pc28-next.localhost:18039/fixture-admin-entry/'+admin_entry,'entry':'http://pc28-next.localhost:18039/fixture-entry/'+entry,'control':(root/'data/local-control-token.txt').read_text()};(Path(__file__).resolve().parent.parent/'data/algorithm-ui-state.json').write_text(json.dumps(state))
 print(state['entry'],flush=True);print(state['admin_entry'],flush=True)
 try:server.serve_forever(poll_interval=.1)
 finally:s.stop.set();server.server_close()
