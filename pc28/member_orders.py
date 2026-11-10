"""Member signal orders and an append-only cash ledger, independent of simulation."""
import json,re,sqlite3,uuid
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
from zoneinfo import ZoneInfo
from .model import utcnow,settlement

SCHEMA='''
CREATE TABLE IF NOT EXISTS member_accounts(user_id INTEGER PRIMARY KEY,principal INTEGER NOT NULL DEFAULT 0,balance INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS member_cash_ledger(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,created TEXT NOT NULL,kind TEXT NOT NULL,amount INTEGER NOT NULL,before_balance INTEGER NOT NULL,after_balance INTEGER NOT NULL,principal_before INTEGER NOT NULL,principal_after INTEGER NOT NULL,order_id INTEGER,actor INTEGER,note TEXT NOT NULL,request_id TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS member_orders(id INTEGER PRIMARY KEY,user_id INTEGER NOT NULL,outbox_id INTEGER NOT NULL UNIQUE,batch_id TEXT,platform_id INTEGER,strategy_name TEXT,target_draw INTEGER NOT NULL,direction TEXT NOT NULL,stake INTEGER NOT NULL,placed_at TEXT NOT NULL,accepted_at TEXT NOT NULL,odds_json TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',draw_time TEXT,result TEXT,total INTEGER,pnl INTEGER,outcome TEXT,settled_at TEXT);
CREATE INDEX IF NOT EXISTS member_orders_user_draw ON member_orders(user_id,target_draw);
CREATE INDEX IF NOT EXISTS member_ledger_user ON member_cash_ledger(user_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS member_ledger_order_kind ON member_cash_ledger(order_id,kind) WHERE order_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS member_finance_migrations(name TEXT PRIMARY KEY,created TEXT NOT NULL);
'''

def cents(value):
 try:
  d=Decimal(str(value))
  if not d.is_finite() or d<=0 or d>100000000 or d*100!=(d*100).to_integral_value():raise ValueError()
 except (InvalidOperation,TypeError,ValueError):raise ValueError('金额须为大于0、最多两位小数的数字，最高100000000U')
 return int(d*100)

def entry(c,uid,kind,amount,principal_delta=0,order_id=None,actor=None,note='',request_id=None,created=None):
 c.execute('INSERT OR IGNORE INTO member_accounts(user_id) VALUES(?)',(uid,))
 a=c.execute('SELECT * FROM member_accounts WHERE user_id=?',(uid,)).fetchone()
 after=a['balance']+amount;principal=a['principal']+principal_delta
 c.execute('INSERT INTO member_cash_ledger(user_id,created,kind,amount,before_balance,after_balance,principal_before,principal_after,order_id,actor,note,request_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(uid,created or utcnow(),kind,amount,a['balance'],after,a['principal'],principal,order_id,actor,note,request_id))
 c.execute('UPDATE member_accounts SET balance=?,principal=? WHERE user_id=?',(after,principal,uid))

def initialize(service):
 with service.lock,service.db.tx() as c:
  for sql in SCHEMA.split(';'):
   if sql.strip():c.execute(sql)
  fields={r['name'] for r in c.execute('PRAGMA table_info(member_orders)')}
  for key in ('follow_account_id','bot_id'):
   if key not in fields:c.execute('ALTER TABLE member_orders ADD COLUMN '+key+' INTEGER')
  c.execute('CREATE INDEX IF NOT EXISTS member_orders_robot_risk ON member_orders(bot_id,status,id)')
  c.execute('UPDATE member_orders SET follow_account_id=(SELECT follow_account_id FROM robot_outbox WHERE id=outbox_id),bot_id=(SELECT bot_id FROM robot_outbox WHERE id=outbox_id) WHERE follow_account_id IS NULL')
  first=not c.execute("SELECT 1 FROM member_finance_migrations WHERE name='initial-members-v1'").fetchone()
  c.execute("INSERT OR IGNORE INTO member_accounts(user_id) SELECT id FROM web_users WHERE role='member'")
  if first:
   users=c.execute('SELECT DISTINCT u.id user_id FROM robot_outbox o LEFT JOIN robot_bindings b ON b.id=o.recipient_id JOIN web_users u ON u.id=COALESCE(o.member_user_id,(SELECT id FROM web_users n WHERE n.username=o.recipient_name),b.user_id) WHERE o.is_test=0 AND u.role=\'member\'').fetchall()
   for u in users:entry(c,u['user_id'],'opening',100000,100000,note='上线初始化：已有推送数据会员本金1000U')
   # Import accepted historical signals once; failed, test and hidden display rows
   # never create duplicate cash events. Hidden successful rows remain accountable.
   rows=c.execute("SELECT o.*,i.batch_id FROM robot_outbox o LEFT JOIN robot_batch_items i ON i.outbox_id=o.id WHERE o.status='sent' AND o.is_test=0 ORDER BY COALESCE(o.finished,o.created),o.id").fetchall()
   for row in rows:
    at=row['sent_at'] if 'sent_at' in row.keys() and row['sent_at'] else row['finished'] or row['created']
    settle(c,at)
    import_signal(c,service,row,row['batch_id'],at,row['finished'] or at)
   settle(c)
   c.execute("INSERT INTO member_finance_migrations VALUES('initial-members-v1',?)",(utcnow(),))

def import_signal(c,service,row,batch_id,placed_at,accepted_at):
 if row['is_test'] or c.execute('SELECT 1 FROM member_orders WHERE outbox_id=?',(row['id'],)).fetchone():return
 binding=c.execute('SELECT user_id FROM robot_bindings WHERE id=?',(row['recipient_id'],)).fetchone()
 frozen_uid=row['member_user_id'] if 'member_user_id' in row.keys() else None
 if not frozen_uid:
  original=c.execute('SELECT id FROM web_users WHERE username=?',(row['recipient_name'],)).fetchone()
  frozen_uid=original['id'] if original else binding['user_id'] if binding else None
 if not frozen_uid:return
 match=re.fullmatch(r'(大|小|单|双)(\d+(?:\.\d{1,2})?)',row['message'].strip())
 if not match:raise ValueError('会员订单无法识别推送方向与金额')
 stake=cents(match[2]);uid=frozen_uid
 if row['push_cents'] is not None and stake!=row['push_cents']:raise ValueError('会员订单金额与推送快照不一致')
 odds=json.loads(row['member_odds']) if 'member_odds' in row.keys() and row['member_odds'] else service.cfg['odds']
 if not ('member_odds' in row.keys() and row['member_odds']):
  table={'legacy':'orders','trend':'trend_orders','custom':'custom_orders'}.get(row['engine'])
  if table:
   old=c.execute('SELECT '+('reason' if row['engine']=='legacy' else 'odds_json')+' FROM '+table+' WHERE id=?',(row['order_id'],)).fetchone()
   if old and old[0]:odds=json.loads(old[0]).get('odds',odds) if row['engine']=='legacy' else json.loads(old[0])
 cur=c.execute('INSERT INTO member_orders(user_id,outbox_id,batch_id,platform_id,strategy_name,target_draw,direction,stake,placed_at,accepted_at,odds_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(uid,row['id'],batch_id,row['platform_id'],row['strategy_name'],row['target_draw'],match[1],stake,placed_at,accepted_at,json.dumps(odds)))
 c.execute('UPDATE member_orders SET follow_account_id=?,bot_id=? WHERE id=?',(row['follow_account_id'] if 'follow_account_id' in row.keys() else None,row['bot_id'] if 'bot_id' in row.keys() else None,cur.lastrowid))
 entry(c,uid,'stake',-stake,order_id=cur.lastrowid,note='接收端成功接收跟单信号',created=accepted_at)

def settle(c,until=None):
 until=until or utcnow()
 rows=c.execute("SELECT o.*,d.total drawn_total,d.size drawn_size,d.parity drawn_parity,d.draw_utc FROM member_orders o JOIN draws d ON d.id=o.target_draw WHERE o.status='pending' AND NOT EXISTS(SELECT 1 FROM conflicts x WHERE x.draw_id=o.target_draw AND x.resolved=0) ORDER BY d.draw_utc,o.id").fetchall()
 for o in rows:
  if not o['draw_utc'] or datetime.fromisoformat(o['draw_utc'])>datetime.fromisoformat(until):continue
  late=datetime.fromisoformat(o['placed_at'])>=datetime.fromisoformat(o['draw_utc'])
  hit=o['direction']==(o['drawn_size'] if o['direction'] in ('大','小') else o['drawn_parity'])
  paid={'pnl':0,'credit':o['stake'],'outcome':'void'} if late else settlement(o['stake'],o['direction'],o['drawn_total'],hit,json.loads(o['odds_json']))
  result=o['drawn_size']+o['drawn_parity']
  c.execute("UPDATE member_orders SET status=?,draw_time=?,result=?,total=?,pnl=?,outcome=?,settled_at=? WHERE id=?",('void' if late else 'settled',o['draw_utc'],result,o['drawn_total'],paid['pnl'],paid['outcome'],utcnow(),o['id']))
  entry(c,o['user_id'],'settle',paid['credit'],order_id=o['id'],note='发送时间不早于开奖，作废退回记账金额' if late else '按对应期开奖结算：'+paid['outcome'],created=max(o['accepted_at'],o['draw_utc']))
  if o['bot_id'] is not None:
   from .robot_risk import check
   check(c,o['bot_id'])

def sync(service):
 with service.lock,service.db.tx() as c:
  c.execute("INSERT OR IGNORE INTO member_accounts(user_id) SELECT id FROM web_users WHERE role='member'")
  settle(c)

def users(service,q,user=None):
 sync(service)
 from .admin import paging
 page,size=paging(q);where="u.role='member' AND u.username LIKE ?";args=('%'+str(q.get('q','')).strip()[:32]+'%',)
 from .auth import member_scope
 scope=member_scope(user)
 if scope is not None:where+=' AND u.id IN (SELECT member_id FROM web_member_access WHERE admin_id=?)';args+=(scope,)
 total=service.db.query('SELECT COUNT(*) n FROM web_users u WHERE '+where,args)[0]['n'];pages=max(1,(total+size-1)//size);page=min(page,pages)
 rows=service.db.query('''SELECT u.id,u.username,u.role,u.active,u.created,u.last_login,u.member_mfa_required,
  EXISTS(SELECT 1 FROM web_totp_credentials mfa WHERE mfa.user_id=u.id) AS mfa_bound,
  COALESCE(a.principal,0) principal,COALESCE(a.balance,0) balance,
 (SELECT COALESCE(SUM(pnl),0) FROM member_orders o WHERE o.user_id=u.id AND o.status='settled') profit,
 (SELECT COUNT(*) FROM member_orders o WHERE o.user_id=u.id AND o.status!='void') follow_count
 FROM web_users u LEFT JOIN member_accounts a ON a.user_id=u.id WHERE '''+where+' ORDER BY u.id DESC LIMIT ? OFFSET ?',(*args,size,(page-1)*size))
 for r in rows:r['roi']=r['profit']/r['principal'] if r['principal'] else None
 return {'rows':rows,'total':total,'page':page,'pages':pages,'page_size':size}

def adjust(service,body,user,ip):
 from .auth import require_member_scope
 amount=cents(body.get('amount'));mode=body.get('mode');uid=body.get('id');note=body.get('note','');rid=body.get('request_id')
 if type(uid)!=int or mode not in ('add','subtract') or not isinstance(note,str) or not 1<=len(note.strip())<=200 or not isinstance(rid,str) or not re.fullmatch('[A-Za-z0-9-]{8,80}',rid):raise ValueError('请填写有效会员、增减方式、备注和请求编号')
 with service.lock,service.db.tx() as c:
  require_member_scope(c,user,uid)
  prior=c.execute('SELECT user_id,kind,amount,note FROM member_cash_ledger WHERE request_id=?',(rid,)).fetchone()
  delta=amount if mode=='add' else -amount
  if prior:
   if (prior['user_id'],prior['kind'],prior['amount'],prior['note'])!=(uid,'adjust',delta,note.strip()):raise ValueError('请求编号已被其他调整使用')
   return {'ok':True}
  if not c.execute("SELECT 1 FROM web_users WHERE id=? AND role='member'",(uid,)).fetchone():raise ValueError('只能调整会员额度')
  c.execute('INSERT OR IGNORE INTO member_accounts(user_id) VALUES(?)',(uid,));a=c.execute('SELECT * FROM member_accounts WHERE user_id=?',(uid,)).fetchone()
  if a['principal']+delta<0 or (delta<0 and a['balance']+delta<0):raise ValueError('减少金额不能超过当前本金或可用余额')
  entry(c,uid,'adjust',delta,delta,actor=user['id'],note=note.strip(),request_id=rid)
  service.auth.audit(c,user['id'],'member_capital_adjust',uid,ip,{'amount_cents':delta,'before_cents':a['balance'],'after_cents':a['balance']+delta,'note':note.strip(),'request_id':rid})
 return {'ok':True}

def create(service,body,user,ip):
 from .auth import grant_member_access,hash_password
 name=body.get('username');encoded=hash_password(body.get('password'))
 if not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9_]{4,32}',name):raise ValueError('用户名须为4—32位字母、数字或下划线')
 with service.lock,service.db.tx() as c:
  if c.execute('SELECT 1 FROM web_users WHERE username=?',(name,)).fetchone():raise ValueError('用户名已存在')
  uid=c.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES(?,?,'member',?)",(name,encoded,utcnow())).lastrowid
  grant_member_access(c,user['id'],uid)
  c.execute('INSERT INTO member_accounts(user_id) VALUES(?)',(uid,));service.auth.audit(c,user['id'],'member_create',uid,ip,{'username':name,'principal':0})
 return {'ok':True,'id':uid}

def time_filter(value):
 try:
  t=datetime.fromisoformat(value)
  if t.tzinfo is None:t=t.replace(tzinfo=ZoneInfo('Asia/Shanghai'))
  return t.astimezone(timezone.utc).isoformat()
 except (ValueError,TypeError):raise ValueError('时间格式无效')

def listing(service,q,ledger=False,user=None):
 sync(service)
 from .admin import paging
 page,size=paging(q);where=['1'];args=[]
 from .auth import member_scope
 scope=member_scope(user)
 if scope is not None:where.append('u.id IN (SELECT member_id FROM web_member_access WHERE admin_id=?)');args.append(scope)
 if q.get('user_id'):
  where.append('o.user_id=?');args.append(int(q['user_id']))
 if q.get('username'):
  where.append('u.username LIKE ?');args.append('%'+str(q['username'])[:32]+'%')
 datecol='o.created' if ledger else 'o.placed_at'
 for key,op in [('start','>='),('end','<=')]:
  if q.get(key):where.append(datecol+op+'?');args.append(time_filter(q[key]))
 if not ledger:
  if q.get('draw'):
   where.append('o.target_draw=?');args.append(int(q['draw']))
  if q.get('direction'):
   if q['direction'] not in ('大','小','单','双'):raise ValueError('订单类型无效')
   where.append('o.direction=?');args.append(q['direction'])
  if q.get('status'):
   if q['status'] not in ('pending','settled','void'):raise ValueError('订单状态无效')
   where.append('o.status=?');args.append(q['status'])
 if q.get('start') and q.get('end') and time_filter(q['start'])>time_filter(q['end']):raise ValueError('开始时间不能晚于结束时间')
 table='member_cash_ledger' if ledger else 'member_orders';source=table+' o JOIN web_users u ON u.id=o.user_id';condition=' AND '.join(where)
 total=service.db.query('SELECT COUNT(*) n FROM '+source+' WHERE '+condition,args)[0]['n'];pages=max(1,(total+size-1)//size);page=min(page,pages)
 columns='o.*,u.username'
 if ledger:columns+=",(SELECT username FROM web_users actor WHERE actor.id=o.actor) actor_name"
 else:columns+=",(SELECT before_balance FROM member_cash_ledger l WHERE l.order_id=o.id AND l.kind='stake') before_balance,(SELECT after_balance FROM member_cash_ledger l WHERE l.order_id=o.id AND l.kind='stake') after_stake_balance,(SELECT after_balance FROM member_cash_ledger l WHERE l.order_id=o.id AND l.kind='settle') after_settle_balance,(SELECT before_balance FROM member_cash_ledger l WHERE l.order_id=o.id AND l.kind='settle') before_settle_balance,(SELECT amount FROM member_cash_ledger l WHERE l.order_id=o.id AND l.kind='settle') settlement_credit,(SELECT a FROM draws d WHERE d.id=o.target_draw) draw_a,(SELECT b FROM draws d WHERE d.id=o.target_draw) draw_b,(SELECT c FROM draws d WHERE d.id=o.target_draw) draw_c"
 rows=service.db.query('SELECT '+columns+' FROM '+source+' WHERE '+condition+' ORDER BY o.id DESC LIMIT ? OFFSET ?',(*args,size,(page-1)*size))
 for r in rows:r.pop('odds_json',None)
 return {'rows':rows,'total':total,'page':page,'pages':pages,'page_size':size}
