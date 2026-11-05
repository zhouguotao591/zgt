"""Explicit receiver destinations; credentials remain encrypted and write-only."""
from pathlib import Path
from urllib.parse import urlsplit
from .bot_secret import SecretStore
from .model import utcnow

class Platforms:
 def init_platforms(self):
  with self.db.tx() as c:
   c.execute('CREATE TABLE IF NOT EXISTS robot_platforms(id INTEGER PRIMARY KEY,name TEXT NOT NULL,endpoint TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,created TEXT NOT NULL,updated TEXT NOT NULL)')
   c.execute("INSERT OR IGNORE INTO robot_platforms VALUES(1,'默认本机工具','',1,?,?)",(utcnow(),utcnow()))
   for table in ('robot_bindings','robot_outbox'):
    if 'platform_id' not in {r[1] for r in c.execute('PRAGMA table_info('+table+')')}:c.execute('ALTER TABLE '+table+' ADD COLUMN platform_id INTEGER NOT NULL DEFAULT 1')
   c.execute('DROP INDEX IF EXISTS robot_binding_user');c.execute('DROP INDEX IF EXISTS robot_binding_target')
   c.execute('CREATE UNIQUE INDEX IF NOT EXISTS robot_binding_platform_user ON robot_bindings(platform_id,user_id) WHERE deleted=0')
   c.execute('CREATE UNIQUE INDEX IF NOT EXISTS robot_binding_platform_target ON robot_bindings(platform_id,account_id,bot_key) WHERE deleted=0')
 def platform_secret(self,key):
  if key==1:return self.secret
  secret=SecretStore(self.service.root);secret.path=Path(self.service.root)/'data/private'/('robot-platform-'+str(key)+'.dpapi');return secret
 def platforms(self):
  from .robot_signals import ENDPOINT
  return [{**r,'endpoint':r['endpoint'] or ENDPOINT,'enabled':bool(r['enabled']),'token_configured':self.platform_secret(r['id']).exists()} for r in self.db.query('SELECT * FROM robot_platforms ORDER BY id')]
 def save_platform(self,body,user=None,ip=''):
  if not isinstance(body,dict) or set(body)-{'id','name','endpoint','enabled','token'}:raise ValueError('平台配置字段无效')
  name=body.get('name');endpoint=body.get('endpoint');enabled=body.get('enabled');key=body.get('id')
  if not isinstance(name,str) or not 1<=len(name.strip())<=60:raise ValueError('平台名称须为1—60个字符')
  if not isinstance(endpoint,str) or len(endpoint)>2000 or any(ch.isspace() for ch in endpoint):raise ValueError('接口地址无效')
  try:
   url=urlsplit(endpoint);port=url.port
   if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment or not url.path.endswith('/api/v1/signals'):raise ValueError()
  except ValueError:raise ValueError('请输入完整HTTP/HTTPS信号接口地址，路径须以 /api/v1/signals 结尾，不含账号密码或查询参数')
  if type(enabled)!=bool or (key is not None and (type(key)!=int or key<1)):raise ValueError('平台开关或编号无效')
  token=body.get('token')
  if token and (not isinstance(token,str) or not 16<=len(token)<=256 or any(ch.isspace() for ch in token) or not token.isascii()):raise ValueError('密钥格式无效')
  with self.service.lock,self.db.tx() as c:
   old=c.execute('SELECT * FROM robot_platforms WHERE id=?',(key,)).fetchone() if key else None
   if key and not old:raise ValueError('推送平台不存在')
   if not key:
    key=c.execute('INSERT INTO robot_platforms(name,endpoint,enabled,created,updated) VALUES(?,?,0,?,?)',(name.strip(),endpoint,utcnow(),utcnow())).lastrowid
   secret=self.platform_secret(key)
   if token:secret.write(token)
   if enabled and not secret.exists():raise ValueError('启用平台前请配置该工具的信号源密钥')
   c.execute('UPDATE robot_platforms SET name=?,endpoint=?,enabled=?,updated=? WHERE id=?',(name.strip(),endpoint,int(enabled),utcnow(),key))
   # Reconfiguration must never redirect old queued orders to another tool.
   c.execute("UPDATE robot_outbox SET status='cancelled',last_error='推送平台配置已更新，不补发旧订单',finished=? WHERE platform_id=? AND status IN ('pending','retry')",(utcnow(),key))
   if user:self.service.auth.audit(c,user['id'],'robot_platform_save',key,ip,{'name':name.strip(),'enabled':enabled,'token_updated':bool(token)})
  return {'ok':True,'id':key}
