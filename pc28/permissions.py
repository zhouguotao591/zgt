"""Per-administrator page/action grants; deny by default, enforced at HTTP boundary."""
import json,re,sqlite3
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from .auth import AuthError,hash_password
from .model import utcnow
PAGES={'admin-overview':'后台总览','strategies':'策略管理','algorithm-hall':'算法大厅','simulation':'模拟账户','history-backtest':'历史回测','admin-ai':'AI概率研究','admin-users':'会员管理','admin-member-orders':'会员订单明细','admin-invites':'邀请码管理','admin-site':'站点设置','admin-ops':'采集与数据','admin-robot':'机器人推送','admin-audit':'安全审计','admin-administrators':'管理员管理','admin-permissions':'权限管理'}
# key, page, label, controls. Read-only navigation/filter/paging belongs to page access.
ACTIONS=[
 ('strategy.publish','strategies','上下架策略','[data-strategy-publish]'),('algorithm.publish','algorithm-hall','上下架算法','[data-algo-publish]'),
 ('strategy.add.reversal','strategies','添加反转策略','#strategy-add'),('strategy.add.hourly','strategies','添加小时策略','#strategy-add-hourly'),('strategy.add.majority','strategies','添加占优策略','#strategy-add-majority'),('strategy.add.single','strategies','添加单边策略','#strategy-add-single'),('strategy.add.previous','strategies','添加上期策略','#strategy-add-previous'),('strategy.add.algorithm','strategies','添加算法跟随策略','#strategy-add-algorithm'),
 ('strategy.add.algorithm_rank','strategies','算法排名自动跟随策略','#strategy-add-rank'),
 ('strategy.edit','strategies','编辑策略参数','[data-strategy-edit]'),('strategy.control','strategies','启用 / 停用策略','[data-strategy-control]'),('strategy.delete','strategies','删除策略','[data-strategy-delete],#strategy-delete-save'),('strategy.backtest','strategies','策略列表发起回测','[data-strategy-backtest]'),
 ('simulation.profit','simulation','查看收益曲线','[data-profit-curve],#profit-refresh,#profit-backfill'),
 ('backtest.chart','history-backtest','切换回测图表周期','[data-k-interval]'),
 ('backtest.run','history-backtest','开始历史回测','#hb-start'),('backtest.export','history-backtest','下载回测文件','#hb-csv,#hb-json'),('ai.train','admin-ai','训练模型','#ai-admin-train'),
 ('member.create','admin-users','添加会员','#member-add,#mf-create-save'),('member.edit','admin-users','编辑会员状态','[data-admin-user],#admin-user-save'),('member.password','admin-users','重置会员密码','[data-admin-reset],#admin-reset-save'),('member.capital','admin-users','增减会员额度','[data-member-capital],#mf-adjust-save'),('member.ledger','admin-users','查看资金记录','[data-member-ledger]'),
 ('member.mfa_policy','admin-users','设置会员登录验证','[data-member-mfa-policy]'),('member.mfa_unbind','admin-users','解除会员验证器绑定','[data-member-mfa-unbind]'),
 ('order.details','admin-member-orders','查看订单详情','[data-mf-order]'),
 ('invite.create','admin-invites','生成邀请码','#admin-invite-add,#admin-invite-submit'),('invite.revoke','admin-invites','作废邀请码','[data-invite-revoke]'),('site.save','admin-site','保存站点设置','#admin-site-save'),
 ('ops.collect','admin-ops','立即采集','[data-admin-task="collect"]'),('ops.archive','admin-ops','历史补采','[data-admin-task="archive"]'),('ops.sync','admin-ops','比对补齐历史','[data-admin-task="sync"]'),('ops.repair_pending','admin-ops','补采待结算开奖','[data-admin-task="repair_pending"]'),('ops.backup','admin-ops','备份数据库','[data-admin-task="backup"]'),('ops.conflict','admin-ops','核验数据冲突','[data-admin-conflict],#admin-conflict-save'),
 ('robot.platform.add','admin-robot','添加推送平台','#robot-platform-add'),('robot.platform.edit','admin-robot','编辑推送平台','[data-platform-edit]'),('robot.platform.control','admin-robot','启用 / 停用推送平台','[data-platform-toggle]'),('robot.binding.add','admin-robot','添加会员订阅','#robot-binding-add'),('robot.binding.edit','admin-robot','编辑会员订阅','[data-binding-edit]'),('robot.control','admin-robot','启用 / 停用订阅','[data-binding-toggle]'),('robot.config','admin-robot','保存推送设置','#robot-config-form button'),('robot.retry','admin-robot','重试消息','[data-robot-retry]'),('robot.delete','admin-robot','删除发送记录','[data-record-delete],#robot-delete-selected'),
 ('robot.account.add','admin-robot','添加下单账号','#ra-add'),('robot.account.edit','admin-robot','编辑账号及默认机器人','[data-ra-edit],[data-ra-default]'),('robot.account.control','admin-robot','启停账号','[data-ra-control]'),('robot.account.delete','admin-robot','归档账号','[data-ra-delete]'),('robot.bot.add','admin-robot','添加机器人','[data-ra-bot-add],#ra-catalog-add'),('robot.bot.edit','admin-robot','编辑机器人','[data-ra-bot-edit],[data-ra-catalog-edit],[data-ra-risk-save]'),('robot.bot.control','admin-robot','启停或删除机器人','[data-ra-bot-control],[data-ra-bot-delete],[data-ra-risk-resume],[data-ra-risk-reconcile]'),('robot.subscribe','admin-robot','配置策略订阅及金额','[data-ra-subs]'),('robot.bulk','admin-robot','批量配置账号','#ra-bulk-enable,#ra-bulk-disable,#ra-bulk-delete,#ra-bulk-subs'),('robot.copy','admin-robot','复制账号配置','[data-ra-copy]'),
 ('administrator.create','admin-administrators','添加管理员','#am-add'),('administrator.control','admin-administrators','启用 / 停用管理员','[data-am-control]'),('administrator.password','admin-administrators','重置管理员密码','[data-am-password]'),('administrator.delete','admin-administrators','删除管理员','[data-am-delete]'),('permission.save','admin-permissions','编辑 / 保存管理员权限','#pm-save,[data-am-permissions]')]

class _Controls(HTMLParser):
 def __init__(self,covered):super().__init__();self.sections=[];self.form=None;self.button=None;self.items=[];self.covered=covered
 def handle_starttag(self,tag,attrs):
  a=dict(attrs)
  if tag=='section':self.sections.append(a.get('id'))
  if tag=='form':self.form=a.get('id')
  if tag!='button' or not self.sections:return
  page=next((x for x in reversed(self.sections) if x in PAGES),None)
  ident=a.get('id');kind=a.get('type','submit')
  if not page or (ident and ident in self.covered) or a.get('data-tab'):return
  if ident:selector='#'+ident;key='button.'+ident
  elif self.form and self.form not in self.covered:
   selector='#'+self.form+' button'+('[type="reset"]' if kind=='reset' else ':not([type="reset"])');key='button.'+self.form+'.'+('reset' if kind=='reset' else 'submit')
  else:return
  self.button={'key':key,'page':page,'selector':selector,'label':''}
 def handle_data(self,text):
  if self.button:self.button['label']+=text.strip()
 def handle_endtag(self,tag):
  if tag=='button' and self.button:
   self.button['label']='按钮：'+(self.button['label'] or self.button['key']);self.items.append(self.button);self.button=None
  if tag=='form':self.form=None
  if tag=='section' and self.sections:self.sections.pop()

@lru_cache(maxsize=1)
def catalog():
 base=[{'key':'page.'+p,'page':p,'label':'访问页面 / 读取数据','selector':'[data-tab="'+p+'"]'} for p in PAGES]+[{'key':k,'page':p,'label':label,'selector':selector} for k,p,label,selector in ACTIONS]
 covered={x for r in base for x in re.findall(r'#([A-Za-z0-9_-]+)',r['selector'])};parser=_Controls(covered)
 for name in ('index.html','admin-sections.html','robot-accounts-section.html','ai-admin-section.html'):
  file=Path(__file__).resolve().parent.parent/'web'/name
  if file.exists():parser.feed(file.read_text(encoding='utf8'))
 for move,label in [('first','首页'),('prev','上一页'),('next','下一页'),('last','尾页')]:
  base.append({'key':'button.member-orders.'+move,'page':'admin-member-orders','label':'按钮：'+label,'selector':'[data-mf-page="orders"][data-move="'+move+'"]'})
 seen={r['key'] for r in base}
 for r in parser.items:
  if r['key'] not in seen:base.append(r);seen.add(r['key'])
 return base

def keys():return {r['key'] for r in catalog()}
def migrate(auth):
 with auth.db.tx() as c:
  c.execute('CREATE TABLE IF NOT EXISTS web_admin_permissions(user_id INTEGER PRIMARY KEY,grants TEXT NOT NULL)')
  c.execute('CREATE TABLE IF NOT EXISTS web_admin_owners(user_id INTEGER PRIMARY KEY)')
  c.execute('CREATE TABLE IF NOT EXISTS web_admin_removed(user_id INTEGER PRIMARY KEY,created TEXT NOT NULL)')
  # Keep the bootstrap owner when the original installation used another name.
  owner=c.execute("SELECT id FROM web_users WHERE role='admin' ORDER BY CASE WHEN lower(username)='admin' THEN 0 ELSE 1 END,id LIMIT 1").fetchone()
  if owner and not c.execute('SELECT 1 FROM web_admin_owners').fetchone():c.execute('INSERT INTO web_admin_owners VALUES(?)',(owner[0],))
  for r in c.execute("SELECT id FROM web_users WHERE role='admin'").fetchall():c.execute('INSERT OR IGNORE INTO web_admin_permissions VALUES(?,?)',(r[0],json.dumps(sorted(keys()))))
def owner(auth,user):return bool(user and user['role']=='admin' and (user['username'].lower()=='admin' or auth.db.query('SELECT 1 FROM web_admin_owners WHERE user_id=?',(user['id'],))))
def grants(auth,user):
 if not user or user['role']!='admin':return set()
 if owner(auth,user):return keys()
 rows=auth.db.query('SELECT grants FROM web_admin_permissions WHERE user_id=?',(user['id'],))
 return set(json.loads(rows[0]['grants'])) & keys() if rows else set()
def allows(auth,user,key):return key in grants(auth,user)
def require(auth,user,key):
 auth.require(user,True)
 if not allows(auth,user,key):raise AuthError('没有此功能权限：'+key,403,'permission_denied')
def any_page(auth,user,pages):
 if not any(allows(auth,user,'page.'+p) for p in pages):raise AuthError('没有访问此页面的权限',403,'permission_denied')
def protect(auth,target):
 if owner(auth,target):raise AuthError('系统主管理员不能通过管理列表修改',403,'protected_administrator')
def checked_grants(value):
 if not isinstance(value,list) or any(not isinstance(x,str) or x not in keys() for x in value):raise ValueError('权限参数无效')
 result=set(value)
 for row in catalog():
  if row['key'] in result and not row['key'].startswith('page.') and 'page.'+row['page'] not in result:raise ValueError('选择功能按钮前请先允许访问对应页面')
 return sorted(result)
def listing(service,q):
 from .admin import page_rows
 result=page_rows(service.db,'web_users','id,username,active,created,last_login',q,"role='admin' AND lower(username)!='admin' AND id NOT IN (SELECT user_id FROM web_admin_removed)")
 for r in result['rows']:r['protected']=owner(service.auth,{**r,'role':'admin'})
 return result

def manage(service,action,body,user,ip):
 auth=service.auth;db=service.db
 if action=='permissions-save':
  require(auth,user,'permission.save');new=checked_grants(body.get('grants'))
  # A delegated administrator can never grant authorities they do not possess.
  if not set(new)<=grants(auth,user):raise AuthError('不能授予超出自身范围的权限',403,'permission_denied')
 else:require(auth,user,{'administrator-create':'administrator.create','administrator-control':'administrator.control','administrator-password':'administrator.password','administrator-delete':'administrator.delete'}[action])
 with service.lock,db.tx() as c:
  if action=='administrator-create':
   name=body.get('username','')
   if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_]{4,32}',name):raise ValueError('用户名须为4—32位字母、数字或下划线')
   name=name.lower()
   if name=='admin':raise ValueError('admin为系统保留账号名称')
   encoded=hash_password(body.get('password'))
   if c.execute('SELECT 1 FROM web_users WHERE username=?',(name,)).fetchone():raise ValueError('用户名已存在')
   uid=c.execute("INSERT INTO web_users(username,password_hash,role,created) VALUES(?,?,'admin',?)",(name,encoded,utcnow())).lastrowid
   c.execute("INSERT INTO web_admin_permissions VALUES(?,'[]')",(uid,));auth.audit(c,user['id'],'administrator_create',uid,ip);return {'ok':True,'id':uid}
  uid=body.get('id');row=c.execute("SELECT * FROM web_users WHERE id=? AND role='admin'",(uid,)).fetchone()
  if not row or c.execute('SELECT 1 FROM web_admin_removed WHERE user_id=?',(uid,)).fetchone():raise ValueError('管理员不存在')
  protect(auth,dict(row))
  if not owner(auth,user) and not grants(auth,dict(row))<=grants(auth,user):raise AuthError('不能管理权限高于自己的管理员',403,'permission_denied')
  if uid==user['id']:raise ValueError('不能通过管理列表修改自己')
  if action=='permissions-save':
   c.execute('INSERT OR REPLACE INTO web_admin_permissions VALUES(?,?)',(uid,json.dumps(new)));details={'grants':new}
  elif action=='administrator-control':
   if type(body.get('active'))!=bool:raise ValueError('状态参数无效')
   c.execute('UPDATE web_users SET active=? WHERE id=?',(int(body['active']),uid));details={'active':body['active']}
  elif action=='administrator-password':c.execute('UPDATE web_users SET password_hash=? WHERE id=?',(hash_password(body.get('password')),uid));details={}
  else:
   # Logical deletion retains audit/order foreign references and prevents re-login.
   c.execute('UPDATE web_users SET active=0 WHERE id=?',(uid,));c.execute("INSERT OR REPLACE INTO web_admin_permissions VALUES(?,'[]')",(uid,));c.execute('INSERT INTO web_admin_removed VALUES(?,?)',(uid,utcnow()));details={'removed_admin':True}
  c.execute('DELETE FROM web_sessions WHERE user_id=?',(uid,));auth.audit(c,user['id'],action.replace('-','_'),uid,ip,details)
 return {'ok':True}

def authorize(service,user,method,path,body=None,query=None):
 auth=service.auth;body=body or {};query=query or {}
 from .auth import is_main_admin
 if path=='/api/admin/robot-notifications':
  if not owner(auth,user):raise AuthError('仅主管理员可管理通知订阅',403,'permission_denied')
  return
 if path=='/api/admin/administrator-members':
  if not is_main_admin(user):raise AuthError('仅 admin 可配置会员可见范围',403,'permission_denied')
  return
 if (path.startswith('/exports/') and path.lower().endswith(('.sqlite3','.sqlite','.db')) or path=='/api/task' and body.get('kind')=='backup') and not is_main_admin(user):
  raise AuthError('仅 admin 可访问完整数据库备份',403,'permission_denied')
 if path=='/api/admin/administrator-password' and not owner(auth,user):
  raise AuthError('仅系统主管理员可重置管理员密码',403,'permission_denied')
 if method=='POST' and path in ('/api/admin/member-mfa-policy','/api/admin/member-mfa-unbind'):
  require(auth,user,'page.admin-users')
  require(auth,user,'member.mfa_policy' if path.endswith('policy') else 'member.mfa_unbind')
  return
 # Assets contain no account data. Account self-service remains available.
 if not path.startswith('/api/') and not path.startswith('/exports/'):return
 if path.startswith('/api/auth/') or path in ('/api/session','/api/status','/api/ai-analysis','/api/draws','/api/tools'):return
 if path=='/api/admin/access':return
 getmap={'/api/admin/algorithms':['algorithm-hall'],'/api/admin/algorithm-detail':['algorithm-hall'],'/api/strategies':['strategies'],'/api/config':['strategies'],'/api/strategy-defaults':['strategies'],'/api/simulation-summary':['simulation'],'/api/trend-status':['strategies'],'/api/strategy-orders':['simulation'],'/api/trend-orders':['simulation'],'/api/history-backtests':['history-backtest','strategies'],'/api/history-backtest-result':['history-backtest','strategies'],'/api/history-backtest-task':['history-backtest','strategies'],'/api/equity':['admin-overview','simulation'],'/api/conflicts':['admin-ops'],'/api/admin/overview':['admin-overview','admin-site','admin-ops'],'/api/admin/users':['admin-users'],'/api/admin/member-orders':['admin-member-orders'],'/api/admin/invites':['admin-invites'],'/api/admin/audit':['admin-audit'],'/api/admin/robot':['admin-robot'],'/api/admin/robot-records':['admin-robot'],'/api/admin/robot-catalog':['admin-robot'],'/api/admin/robot-accounts':['admin-robot'],'/api/admin/robot-account':['admin-robot'],'/api/admin/robot-batch':['admin-robot'],'/api/admin/ai':['admin-ai'],'/api/admin/administrators':['admin-administrators','admin-permissions'],'/api/admin/permissions':['admin-permissions']}
 if method=='GET':
  if path=='/api/strategy-profit-curve':require(auth,user,'page.simulation');require(auth,user,'simulation.profit');return
  if path=='/api/admin/member-ledger':require(auth,user,'page.admin-users');require(auth,user,'member.ledger');return
  if path.startswith('/exports/'):
   require(auth,user,'page.history-backtest');require(auth,user,'backtest.export');return
  if path in getmap:
   any_page(auth,user,getmap[path])
   if path=='/api/admin/users' and query.get('q'):require(auth,user,'button.admin-user-search.submit')
   if path=='/api/admin/member-orders' and any(query.get(k) for k in ('start','end','draw','username','direction','status')):require(auth,user,'button.mf-orders-filter.submit')
   return
 else:
  if path=='/api/admin/robot-bot-risk':
   require(auth,user,'page.admin-robot')
   require(auth,user,'robot.bot.control' if body.get('action') in ('resume','reconcile') else 'robot.bot.edit')
   return
  actionmap={'/api/admin/strategy-publish':'strategy.publish','/api/admin/algorithm-publish':'algorithm.publish','/api/strategy-control':'strategy.control','/api/trend-control':'strategy.control','/api/strategy-delete':'strategy.delete','/api/pause':'strategy.control','/api/admin/member-create':'member.create','/api/admin/member-capital':'member.capital','/api/admin/user-update':'member.edit','/api/admin/user-password':'member.password','/api/admin/invite-create':'invite.create','/api/admin/invite-revoke':'invite.revoke','/api/admin/site':'site.save','/api/admin/ai-train':'ai.train','/api/admin/robot-platform-save':'robot.platform','/api/admin/robot-binding-save':'robot.binding','/api/admin/robot-binding-control':'robot.control','/api/admin/robot-config':'robot.config','/api/admin/robot-retry':'robot.retry','/api/admin/robot-records-delete':'robot.delete','/api/resolve-conflict':'ops.conflict','/api/admin/administrator-create':'administrator.create','/api/admin/administrator-control':'administrator.control','/api/admin/administrator-password':'administrator.password','/api/admin/administrator-delete':'administrator.delete','/api/admin/permissions-save':'permission.save'}
  if path in ('/api/admin/robot-catalog-save','/api/admin/robot-account-save','/api/admin/robot-account-control','/api/admin/robot-account-delete','/api/admin/robot-bot-save','/api/admin/robot-bot-control','/api/admin/robot-subscriptions','/api/admin/robot-account-bulk'):
   key={'/api/admin/robot-catalog-save':'robot.bot.edit' if body.get('id') else 'robot.bot.add','/api/admin/robot-account-save':'robot.account.edit' if body.get('id') else 'robot.account.add','/api/admin/robot-account-control':'robot.account.control','/api/admin/robot-account-delete':'robot.account.delete','/api/admin/robot-bot-save':'robot.bot.edit' if body.get('bot',{}).get('id') else 'robot.bot.add','/api/admin/robot-bot-control':'robot.bot.control','/api/admin/robot-subscriptions':'robot.subscribe','/api/admin/robot-account-bulk':'robot.bulk'}[path]
   if path.endswith('account-save') and body.get('copy_from'):require(auth,user,'robot.copy')
   if path.endswith('bot-save') and body.get('make_default'):require(auth,user,'robot.account.edit')
   if path.endswith('bot-save') and body.get('bot',{}).get('id'):
    current=service.db.query('SELECT enabled FROM robot_bots WHERE id=?',(body['bot']['id'],))
    if current and 'enabled' in body['bot'] and bool(current[0]['enabled'])!=body['bot']['enabled']:require(auth,user,'robot.bot.control')
   if path.endswith('account-save') and body.get('id'):
    old=service.db.query('SELECT enabled FROM robot_accounts WHERE id=?',(body['id'],))
    if old and 'enabled' in body and bool(old[0]['enabled'])!=body['enabled']:require(auth,user,'robot.account.control')
   if path.endswith('account-bulk'):
    required={'enable':'robot.account.control','disable':'robot.account.control','delete':'robot.account.delete','subscriptions':'robot.subscribe'}.get(body.get('operation'))
    if required:require(auth,user,required)
  elif path in ('/api/admin/robot-platform-save','/api/admin/robot-binding-save'):
   platform=path.endswith('platform-save');key=('robot.platform.' if platform else 'robot.binding.')+('edit' if body.get('id') else 'add')
   if body.get('id'):
    rows=service.db.query('SELECT * FROM '+('robot_platforms' if platform else 'robot_bindings')+' WHERE id=?',(body['id'],))
    if platform and rows and 'execution_required' in body and bool(body['execution_required'])!=bool(rows[0]['execution_required']):
     require(auth,user,'robot.platform.control')
    if rows and 'enabled' in body and bool(body['enabled'])!=bool(rows[0]['enabled']):
     control='robot.platform.control' if platform else 'robot.control'
     require(auth,user,'page.admin-robot');require(auth,user,control)
     if platform:
      from .robot_signals import ENDPOINT
      current={**rows[0],'endpoint':rows[0]['endpoint'] or ENDPOINT}
      if set(body)<={'id','name','endpoint','enabled','version'} and all(body.get(k,current[k])==current[k] for k in ('name','endpoint')):key=control
  elif path=='/api/strategy-save':
   if body.get('id'):key='strategy.edit'
   else:
    s=body.get('settings',{});kind='reversal' if 'fixed' in s else s.get('strategy_type','reversal');key='strategy.add.'+kind
  elif path=='/api/task':
   kind=body.get('kind');key=('strategy.backtest' if allows(auth,user,'strategy.backtest') else 'backtest.run') if kind=='history_backtest' else 'ops.'+str(kind)
  elif path=='/api/config':
   # Whole configuration writes can change every strategy and execution flag.
   if not owner(auth,user):raise AuthError('完整配置写入仅系统主管理员可用',403,'permission_denied')
   return
  else:key=actionmap.get(path)
  if key:
   spec=next((r for r in catalog() if r['key']==key),None)
   if not spec:raise AuthError('不允许的管理操作',403,'permission_denied')
   require(auth,user,'page.'+spec['page']);require(auth,user,key);return
 # New/unclassified endpoints are never implicitly granted to delegated admins.
 if not owner(auth,user):raise AuthError('此接口未获授权',403,'permission_denied')
