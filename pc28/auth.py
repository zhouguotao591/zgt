"""Local membership, invite-only registration, one-use image captcha and RBAC."""
import base64,hashlib,hmac,json,re,secrets,time,struct,zlib
from http.cookies import SimpleCookie
from pathlib import Path
from .model import utcnow
SCHEMA="""
CREATE TABLE IF NOT EXISTS web_users(id INTEGER PRIMARY KEY,username TEXT NOT NULL COLLATE NOCASE UNIQUE,password_hash TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','member')),active INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL,last_login TEXT);
CREATE TABLE IF NOT EXISTS web_sessions(token_hash TEXT PRIMARY KEY,user_id INTEGER,csrf TEXT NOT NULL,expires INTEGER NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS web_captchas(id TEXT PRIMARY KEY,session_hash TEXT NOT NULL,answer_hash TEXT NOT NULL,expires INTEGER NOT NULL,used INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS web_invites(id INTEGER PRIMARY KEY,code_hash TEXT NOT NULL UNIQUE,note TEXT NOT NULL,max_uses INTEGER NOT NULL,used INTEGER NOT NULL DEFAULT 0,expires INTEGER NOT NULL,active INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL,created_by INTEGER);
CREATE TABLE IF NOT EXISTS web_invite_uses(invite_id INTEGER,user_id INTEGER UNIQUE,created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS web_auth_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS web_auth_events(id INTEGER PRIMARY KEY,created TEXT NOT NULL,actor INTEGER,action TEXT NOT NULL,target TEXT,ip TEXT,details TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS web_auth_limits(key TEXT NOT NULL,at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS web_auth_limits_key_at ON web_auth_limits(key,at);
"""
class AuthError(ValueError):
 def __init__(self,message,status=400,code='auth_error'):super().__init__(message);self.status=status;self.code=code

def digest(s):return hashlib.sha256(s.encode()).hexdigest()
def hash_password(password):
 if not isinstance(password,str) or not 10<=len(password)<=128:raise AuthError('密码长度须为10—128位')
 salt=secrets.token_bytes(16);key=hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
 return 'scrypt$'+salt.hex()+'$'+key.hex()
def check_password(password,encoded):
 try:
  if not isinstance(password,str) or len(password)>128:return False
  _,salt,key=encoded.split('$');actual=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32)
  return hmac.compare_digest(actual.hex(),key)
 except (ValueError,TypeError):return False

def captcha_png(answer):
 # Dependency-free raster captcha: no answer is returned in SVG/text/metadata.
 glyphs=['11111100011000110001100011000111111','00100011000010000100001000010001110','11111000010000111111100001000011111','11111000010000101111000010000111111','10001100011000111111000010000100001','11111100001000011111000010000111111','11111100001000011111100011000111111','11111000010001000100010000100001000','11111100011000111111100011000111111','11111100011000111111000010000111111']
 w,h=176,54;pixels=[[(246,249,252) for _ in range(w)] for _ in range(h)]
 for _ in range(900):pixels[secrets.randbelow(h)][secrets.randbelow(w)]=(secrets.randbelow(100)+120,170,200)
 for i,ch in enumerate(answer):
  gx=9+i*32;gy=9+secrets.randbelow(7);color=(20+secrets.randbelow(50),65+secrets.randbelow(40),100+secrets.randbelow(50));g=glyphs[int(ch)]
  for y in range(7):
   for x in range(5):
    if g[y*5+x]=='1':
     for dy in range(4):
      for dx in range(4):pixels[gy+y*4+dy][gx+x*4+dx+(y%2)]=color
 for _ in range(3):
  y=secrets.randbelow(h)
  for x in range(w):pixels[(y+x//12)%h][x]=(130,160,190)
 raw=b''.join(b'\0'+bytes(v for pixel in row for v in pixel) for row in pixels)
 def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')

class Auth:
 def __init__(self,service):
  self.service=service;self.db=service.db;self.root=service.root
  with self.db.tx() as c:
   for sql in SCHEMA.split(';'):
    if sql.strip():c.execute(sql)
   c.execute("INSERT OR IGNORE INTO web_auth_settings VALUES('registration_enabled','true')")
   c.execute("INSERT OR IGNORE INTO web_auth_settings VALUES('site_name','PC28 本地研究台')")
   for key,filename in [('setup_hash','admin-setup-code.txt'),('control_hash','local-control-token.txt')]:
    if not c.execute('SELECT 1 FROM web_auth_settings WHERE key=?',(key,)).fetchone():
     secret=secrets.token_urlsafe(32);folder=self.root/'data';folder.mkdir(parents=True,exist_ok=True);(folder/filename).write_text(secret,encoding='utf-8');c.execute('INSERT INTO web_auth_settings VALUES(?,?)',(key,digest(secret)))
  with self.db.tx() as c:
   if 'scope' not in {r[1] for r in c.execute('PRAGMA table_info(web_sessions)')}:c.execute("ALTER TABLE web_sessions ADD COLUMN scope TEXT NOT NULL DEFAULT 'member'")
  self.dummy=hash_password(secrets.token_urlsafe(24))
 def setting(self,key):return self.db.query('SELECT value FROM web_auth_settings WHERE key=?',(key,))[0]['value']
 def setup_needed(self):return not self.db.query('SELECT 1 FROM web_users LIMIT 1')
 def public_user(self,user):return {k:user[k] for k in ('id','username','role')} if user else None
 def audit(self,c,actor,action,target='',ip='',details=None):c.execute('INSERT INTO web_auth_events(created,actor,action,target,ip,details) VALUES(?,?,?,?,?,?)',(utcnow(),actor,action,str(target),ip,json.dumps(details or {},ensure_ascii=False)))
 def limit(self,c,key,maximum,window):
  now=int(time.time());c.execute('DELETE FROM web_auth_limits WHERE at<?',(now-86400,))
  count=c.execute('SELECT COUNT(*) FROM web_auth_limits WHERE key=? AND at>?',(key,now-window)).fetchone()[0]
  if count>=maximum:raise AuthError('操作过于频繁，请稍后重试',429,'rate_limited')
 def record_limit(self,c,key):c.execute('INSERT INTO web_auth_limits VALUES(?,?)',(key,int(time.time())))
 def session(self,cookie,scope='member'):
  try:
   jar=SimpleCookie();jar.load(cookie or '');token=jar.get('pc28_admin_session' if scope=='admin' else 'pc28_session');token=token.value if token else ''
  except Exception:return None,None
  if not token:return None,None
  rows=self.db.query('SELECT * FROM web_sessions WHERE token_hash=? AND expires>? AND scope=?',(digest(token),int(time.time()),scope))
  if not rows:return None,None
  row=rows[0];user=None
  if row['user_id']:
   users=self.db.query('SELECT * FROM web_users WHERE id=? AND active=1',(row['user_id'],))
   if not users:return None,None
   user=users[0]
  return row,user
 def create_session(self,user_id=None,old=None,scope='member'):
  token=secrets.token_urlsafe(32);now=int(time.time());expires=now+(43200 if user_id else 1800);row={'token_hash':digest(token),'user_id':user_id,'csrf':secrets.token_urlsafe(32),'expires':expires,'created':now,'scope':scope}
  with self.db.tx() as c:
   c.execute('DELETE FROM web_sessions WHERE expires<=?',(now,));c.execute('DELETE FROM web_captchas WHERE expires<=?',(now,))
   if old:c.execute('DELETE FROM web_sessions WHERE token_hash=?',(old['token_hash'],))
   c.execute('INSERT INTO web_sessions(token_hash,user_id,csrf,expires,created,scope) VALUES(?,?,?,?,?,?)',tuple(row[k] for k in ('token_hash','user_id','csrf','expires','created','scope')))
  return row,('pc28_admin_session=' if scope=='admin' else 'pc28_session=')+token+('; Path=/admin;' if scope=='admin' else '; Path=/;')+' HttpOnly; SameSite=Strict; Max-Age='+str(expires-now)
 def guest_session(self,ip,scope='member'):
  with self.db.tx() as c:
   self.limit(c,'session:'+ip,60,300);self.record_limit(c,'session:'+ip)
  return self.create_session(scope=scope)
 def require(self,user,admin=False):
  if not user:raise AuthError('请先登录',401,'login_required')
  if admin and user['role']!='admin':raise AuthError('仅管理员可使用此功能',403,'admin_required')
 def csrf(self,session,token):
  if not session or not hmac.compare_digest(token or '',session['csrf']):raise AuthError('会话校验已过期，请刷新后重试',403,'invalid_session')
 def captcha(self,session,ip):
  answer=''.join(str(secrets.randbelow(10)) for _ in range(5));key=secrets.token_urlsafe(24)
  with self.db.tx() as c:
   self.limit(c,'captcha:'+ip,40,300);self.record_limit(c,'captcha:'+ip)
   c.execute('UPDATE web_captchas SET used=1 WHERE session_hash=?',(session['token_hash'],));c.execute('INSERT INTO web_captchas VALUES(?,?,?,?,0)',(key,session['token_hash'],digest(key+answer),int(time.time())+120))
  return {'id':key,'image':'data:image/png;base64,'+base64.b64encode(captcha_png(answer)).decode(),'expires_in':120}
 def consume_captcha(self,session,body):
  with self.db.tx() as c:
   row=c.execute('SELECT * FROM web_captchas WHERE id=? AND session_hash=? AND used=0 AND expires>?',(str(body.get('captcha_id','')),session['token_hash'],int(time.time()))).fetchone()
   if row:c.execute('UPDATE web_captchas SET used=1 WHERE id=?',(row['id'],))
  if not row or not hmac.compare_digest(row['answer_hash'],digest(row['id']+str(body.get('captcha','')).strip())):raise AuthError('验证码错误或已过期，请刷新验证码')
 def authenticate(self,kind,session,body,ip):
  scope=session.get('scope','member')
  if (kind=='setup' and scope!='admin') or (kind=='register' and scope=='admin'):raise AuthError('此入口不支持该操作',403)
  self.consume_captcha(session,body)
  username=body.get('username','');password=body.get('password','')
  if not isinstance(username,str) or not re.fullmatch(r'[A-Za-z0-9_]{4,32}',username):raise AuthError('用户名须为4—32位字母、数字或下划线')
  now=int(time.time());username=username.lower()
  with self.db.tx() as c:self.limit(c,'auth:'+ip,20,600);self.limit(c,'user:'+username,10,600)
  if kind=='login':
   rows=self.db.query('SELECT * FROM web_users WHERE username=?',(username,));user=rows[0] if rows else None;valid=check_password(password,user['password_hash'] if user else self.dummy)
   if not valid or not user or not user['active'] or (scope=='admin' and user['role']!='admin'):
    with self.db.tx() as c:
     self.record_limit(c,'auth:'+ip);self.record_limit(c,'user:'+username);self.audit(c,None,'login_failed',username,ip)
    raise AuthError('用户名或密码不正确，或账号已停用',401,'invalid_credentials')
  else:
   encoded=hash_password(password)
   with self.db.tx() as c:
    self.record_limit(c,'auth:'+ip)
    if kind=='setup':
     if c.execute('SELECT 1 FROM web_users').fetchone():raise AuthError('管理员已经初始化',409)
     secret=self.setting('setup_hash')
     if not hmac.compare_digest(digest(str(body.get('setup_code',''))),secret):raise AuthError('本机初始化码不正确')
     role='admin';invite=None
    else:
     if self.setting('registration_enabled')!='true':raise AuthError('暂未开放会员注册',403)
     code=str(body.get('invite_code','')).strip();invite=c.execute('SELECT * FROM web_invites WHERE code_hash=? AND active=1 AND expires>? AND used<max_uses',(digest(code),now)).fetchone()
     if not invite:raise AuthError('邀请码无效、已过期或使用次数已满')
     role='member'
    if c.execute('SELECT 1 FROM web_users WHERE username=?',(username,)).fetchone():raise AuthError('该用户名已被注册')
    cur=c.execute('INSERT INTO web_users(username,password_hash,role,created) VALUES(?,?,?,?)',(username,encoded,role,utcnow()));uid=cur.lastrowid
    if invite:
     c.execute('UPDATE web_invites SET used=used+1 WHERE id=?',(invite['id'],));c.execute('INSERT INTO web_invite_uses VALUES(?,?,?)',(invite['id'],uid,utcnow()))
    self.audit(c,uid,'admin_setup' if kind=='setup' else 'register',uid,ip,{'invite_id':invite['id'] if invite else None})
   user=self.db.query('SELECT * FROM web_users WHERE id=?',(uid,))[0]
  with self.db.tx() as c:
   c.execute('UPDATE web_users SET last_login=? WHERE id=?',(utcnow(),user['id']));c.execute('DELETE FROM web_auth_limits WHERE key=?',('user:'+username,));self.audit(c,user['id'],'login',user['id'],ip)
  row,cookie=self.create_session(user['id'],session,scope=scope);return {'ok':True,'user':self.public_user(user),'token':row['csrf']},cookie
 def control_allowed(self,token):return bool(token) and hmac.compare_digest(digest(token),self.setting('control_hash'))
 def logout(self,session,user,ip):
  with self.db.tx() as c:
   c.execute('DELETE FROM web_sessions WHERE token_hash=?',(session['token_hash'],));self.audit(c,user['id'] if user else None,'logout','',ip)
  return ('pc28_admin_session=; Path=/admin;' if session.get('scope')=='admin' else 'pc28_session=; Path=/;')+' HttpOnly; SameSite=Strict; Max-Age=0'
 def change_password(self,user,body,ip):
  if not check_password(body.get('current_password'),user['password_hash']):raise AuthError('原密码不正确')
  encoded=hash_password(body.get('new_password'))
  with self.db.tx() as c:
   c.execute('UPDATE web_users SET password_hash=? WHERE id=?',(encoded,user['id']));c.execute('DELETE FROM web_sessions WHERE user_id=?',(user['id'],));self.audit(c,user['id'],'password_change',user['id'],ip)
  return {'ok':True}
