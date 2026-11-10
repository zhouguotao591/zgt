"""TOTP enrollment, verification and recovery for protected login scopes."""
import base64
import csv
import getpass
import hashlib
import hmac
import io
import os
import re
import secrets
import struct
import subprocess
import time
from urllib.parse import quote, urlencode

from .auth import AuthError, check_password, digest, require_member_scope
from .model import utcnow


SCHEMA = (
    """CREATE TABLE IF NOT EXISTS web_totp_credentials(
       user_id INTEGER PRIMARY KEY REFERENCES web_users(id) ON DELETE CASCADE,
       secret BLOB NOT NULL, bound_at TEXT NOT NULL, last_step INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS web_mfa_challenges(
       session_hash TEXT PRIMARY KEY REFERENCES web_sessions(token_hash) ON DELETE CASCADE,
       user_id INTEGER NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,
       mode TEXT NOT NULL, expires INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
       pending_secret BLOB)""",
    "CREATE INDEX IF NOT EXISTS ix_web_mfa_challenges_user ON web_mfa_challenges(user_id)",
    """CREATE TABLE IF NOT EXISTS web_mfa_recovery_codes(
       user_id INTEGER NOT NULL REFERENCES web_users(id) ON DELETE CASCADE,
       code_hash TEXT NOT NULL, expires INTEGER, PRIMARY KEY(user_id,code_hash))""",
    """CREATE TRIGGER IF NOT EXISTS web_mfa_account_changed
       AFTER UPDATE OF password_hash,active,role,member_mfa_required ON web_users
       WHEN OLD.password_hash!=NEW.password_hash OR OLD.active!=NEW.active
         OR OLD.role!=NEW.role OR OLD.member_mfa_required!=NEW.member_mfa_required
       BEGIN
         DELETE FROM web_sessions WHERE token_hash IN
           (SELECT session_hash FROM web_mfa_challenges WHERE user_id=NEW.id);
         DELETE FROM web_sessions WHERE user_id=NEW.id AND scope='member_center';
       END""",
)


def migrate(c):
    for table, name in [('web_users', 'member_mfa_required'), ('web_sessions', 'mfa_verified')]:
        if name not in {row[1] for row in c.execute(f'PRAGMA table_info({table})')}:
            c.execute(f'ALTER TABLE {table} ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
    for sql in SCHEMA:
        c.execute(sql)


def required(scope, user):
    return scope == 'admin' or (scope == 'member_center' and user['member_mfa_required'])


def code_at(secret, step):
    mac = hmac.digest(secret, struct.pack('>Q', step), 'sha1')
    offset = mac[-1] & 15
    number = int.from_bytes(mac[offset:offset + 4], 'big') & 0x7fffffff
    return f'{number % 1000000:06d}'


class MFA:
    def __init__(self, auth):
        self.auth = auth
        self.key_path = auth.root / 'data/private/mfa.key'

    def key_init(self):
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as f:
            if os.name == 'nt':
                try:
                    identity = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True, errors='replace')
                    sid = next(csv.reader(io.StringIO(identity.strip())))[1]
                    subprocess.run(['icacls', str(self.key_path), '/inheritance:r', '/grant:r', f'*{sid}:(F)'],
                                   check=True, capture_output=True)
                except (OSError, subprocess.CalledProcessError, IndexError, StopIteration):
                    raise AuthError('无法限制密钥文件的访问权限；请检查 Windows 账户和 ACL 后重试') from None
            f.write(secrets.token_bytes(32))

    def cipher(self):
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            key = self.key_path.read_bytes()
            if len(key) != 32:
                raise ValueError('invalid key length')
            return AESGCM(key)
        except (ImportError, OSError, ValueError):
            raise AuthError('两步验证密钥不可用，请联系运维', 503, 'mfa_unavailable') from None

    def encrypt(self, secret, uid):
        nonce = secrets.token_bytes(12)
        return nonce + self.cipher().encrypt(nonce, secret, str(uid).encode())

    def decrypt(self, encrypted, uid):
        from cryptography.exceptions import InvalidTag
        try:
            return self.cipher().decrypt(encrypted[:12], encrypted[12:], str(uid).encode())
        except InvalidTag:
            raise AuthError('两步验证密钥不可用，请联系运维', 503, 'mfa_unavailable') from None

    def begin(self, c, user, session, mode):
        row, cookie = self.auth.write_session(c, old=session, scope=session['scope'])
        c.execute('INSERT INTO web_mfa_challenges(session_hash,user_id,mode,expires) VALUES(?,?,?,?)',
                  (row['token_hash'], user['id'], mode, int(time.time()) + 300))
        return {'ok': True, 'status': 'mfa_' + mode + '_required', 'token': row['csrf']}, cookie

    def matched_step(self, encrypted, uid, code, last_step):
        if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
            return None
        secret = self.decrypt(encrypted, uid)
        step = int(time.time()) // 30
        return next((candidate for candidate in (step, step - 1, step + 1)
                     if candidate > last_step and hmac.compare_digest(code, code_at(secret, candidate))), None)

    def setup_data(self, secret, user):
        try:
            import qrcode
            from qrcode.image.pure import PyPNGImage
        except ImportError:
            raise AuthError('二维码依赖未安装，请联系运维', 503, 'mfa_unavailable') from None
        key = base64.b32encode(secret).decode()
        issuer = self.auth.setting('site_name').replace(':', ' ')[:50] or 'PC28'
        label = quote(issuer + ':' + user['username'], safe='')
        uri = 'otpauth://totp/' + label + '?' + urlencode({'secret': key, 'issuer': issuer})
        image = io.BytesIO()
        qrcode.make(uri, image_factory=PyPNGImage).save(image)
        return {'key': key, 'image': 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode()}

    def recovery_codes(self, c, uid):
        codes = [secrets.token_hex(12) for _ in range(10)]
        c.execute('DELETE FROM web_mfa_recovery_codes WHERE user_id=?', (uid,))
        c.executemany('INSERT INTO web_mfa_recovery_codes(user_id,code_hash) VALUES(?,?)',
                      ((uid, digest(code)) for code in codes))
        return codes

    def revoke(self, c, uid, scope):
        c.execute('DELETE FROM web_sessions WHERE scope=? AND (user_id=? OR token_hash IN '
                  '(SELECT session_hash FROM web_mfa_challenges WHERE user_id=?))', (scope, uid, uid))

    def status(self, scope, user):
        return {
            'required': bool(required(scope, user)),
            'bound': bool(self.auth.db.query('SELECT 1 FROM web_totp_credentials WHERE user_id=?', (user['id'],))),
        }

    def self_service(self, session, user, action, body, ip):
        if not session['mfa_verified'] or not required(session['scope'], user):
            raise AuthError('请先通过两步验证登录', 403)
        uid = user['id']
        password = body.get('password')
        valid_password = isinstance(password, str) and check_password(password, user['password_hash'])
        with self.auth.db.tx() as c:
            active = c.execute('SELECT 1 FROM web_sessions s JOIN web_users u ON u.id=s.user_id '
                               'WHERE s.token_hash=? AND s.expires>? AND u.active=1 AND u.password_hash=?',
                               (session['token_hash'], int(time.time()), user['password_hash'])).fetchone()
            if not active:
                raise AuthError('登录状态已变化，请重新登录', 401, 'mfa_expired')
            if not valid_password:
                key = f'mfa:{uid}'
                self.auth.limit(c, key, 10, 900)
                self.auth.record_limit(c, key)
                self.auth.audit(c, uid, 'mfa_failed', uid, ip)
            elif self.reauthenticate(c, uid, body.get('code'), ip):
                if action == 'rotate':
                    return self.begin(c, user, session, 'rotate')
                codes = self.recovery_codes(c, uid)
                self.auth.audit(c, uid, 'mfa_recovery_regenerated', uid, ip)
                return {'recovery_codes': codes}, None
        raise AuthError('密码或动态码错误，或动态码已使用', 401, 'mfa_invalid')

    def challenge(self, session, action, body, ip):
        if action not in ('start', 'confirm', 'verify', 'recover', 'cancel'):
            raise AuthError('未知验证操作', 404)
        now = int(time.time())
        with self.auth.db.tx() as c:
            challenge = c.execute('SELECT * FROM web_mfa_challenges WHERE session_hash=? AND expires>?',
                                  (session['token_hash'], now)).fetchone()
            if not challenge or session['user_id'] is not None:
                raise AuthError('验证已过期，请重新登录', 401, 'mfa_expired')
            uid, scope = challenge['user_id'], session['scope']
            user = c.execute('SELECT * FROM web_users WHERE id=? AND active=1', (uid,)).fetchone()
            if not user or user['role'] != ('admin' if scope == 'admin' else 'member') or not required(scope, user):
                raise AuthError('登录状态已变化，请重新登录', 401, 'mfa_expired')
            credential = c.execute('SELECT * FROM web_totp_credentials WHERE user_id=?', (uid,)).fetchone()
            mode = challenge['mode']
            if action == 'cancel':
                c.execute('DELETE FROM web_sessions WHERE token_hash=?', (session['token_hash'],))
                return {'ok': True}, None
            if mode != ('verify' if credential else 'enroll') and not (mode == 'rotate' and credential):
                raise AuthError('登录状态已变化，请重新登录', 401, 'mfa_expired')
            if action == 'start' and mode in ('enroll', 'rotate'):
                secret = self.decrypt(challenge['pending_secret'], uid) if challenge['pending_secret'] else secrets.token_bytes(20)
                if not challenge['pending_secret']:
                    c.execute('UPDATE web_mfa_challenges SET pending_secret=? WHERE session_hash=?',
                              (self.encrypt(secret, uid), session['token_hash']))
                return self.setup_data(secret, user), None
            if (mode == 'verify' and action not in ('verify', 'recover')
                    or mode != 'verify' and (action != 'confirm' or not challenge['pending_secret'])):
                raise AuthError('验证步骤无效', 409)
            limit_key = f'mfa:{uid}'
            source_key = f'mfa-source:{ip}'
            deployment = self.auth.service.cfg.get('deployment', {})
            proxy_loopback = ip in ('127.0.0.1', '::1') and (
                deployment.get('public_origin') or deployment.get('admin_origin'))
            self.auth.limit(c, limit_key, 10, 900)
            if not proxy_loopback:
                self.auth.limit(c, source_key, 100, 900)
            if action == 'recover':
                code = body.get('code')
                used = isinstance(code, str) and re.fullmatch(r'[0-9a-f]{24}', code) and c.execute(
                    'DELETE FROM web_mfa_recovery_codes WHERE user_id=? AND code_hash=? AND (expires IS NULL OR expires>?)',
                    (uid, digest(code), now)).rowcount
                if used:
                    self.auth.audit(c, uid, 'mfa_recovery_used', uid, ip)
                    return self.begin(c, user, session, 'rotate')
            else:
                encrypted = credential['secret'] if mode == 'verify' else challenge['pending_secret']
                step = self.matched_step(encrypted, uid, body.get('code'), credential['last_step'] if mode == 'verify' else -1)
                if step is not None:
                    if mode == 'verify':
                        c.execute('UPDATE web_totp_credentials SET last_step=? WHERE user_id=?', (step, uid))
                        return self.auth.finish_login(c, user, session, ip, verified=True)
                    self.revoke(c, uid, scope)
                    c.execute('INSERT INTO web_totp_credentials VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE '
                              'SET secret=excluded.secret,bound_at=excluded.bound_at,last_step=excluded.last_step',
                              (uid, encrypted, utcnow(), step))
                    codes = self.recovery_codes(c, uid)
                    self.auth.audit(c, uid, 'mfa_bound' if mode == 'enroll' else 'mfa_rotated', uid, ip)
                    result, cookie = self.auth.finish_login(c, user, session, ip, verified=True)
                    return {**result, 'recovery_codes': codes}, cookie
            self.auth.record_limit(c, limit_key)
            if not proxy_loopback:
                self.auth.record_limit(c, source_key)
            self.auth.audit(c, uid, 'mfa_failed', uid, ip, {'scope': scope})
            if challenge['attempts'] >= 4:
                c.execute('DELETE FROM web_sessions WHERE token_hash=?', (session['token_hash'],))
            else:
                c.execute('UPDATE web_mfa_challenges SET attempts=attempts+1 WHERE session_hash=?', (session['token_hash'],))
        if challenge['attempts'] >= 4:
            raise AuthError('尝试次数已用完，请重新登录', 429, 'mfa_expired')
        raise AuthError('验证码错误、已过期或已使用', 401, 'mfa_invalid')

    def reauthenticate(self, c, uid, code, ip):
        key = f'mfa:{uid}'
        self.auth.limit(c, key, 10, 900)
        row = c.execute('SELECT * FROM web_totp_credentials WHERE user_id=?', (uid,)).fetchone()
        step = self.matched_step(row['secret'], uid, code, row['last_step']) if row else None
        if step is None:
            self.auth.record_limit(c, key)
            self.auth.audit(c, uid, 'mfa_failed', uid, ip)
            return False
        c.execute('UPDATE web_totp_credentials SET last_step=? WHERE user_id=?', (step, uid))
        return True

    def manage_member(self, action, actor, session, body, ip):
        uid = body.get('id')
        enabled = body.get('enabled')
        reason = body.get('reason', '')
        if type(uid) is not int or uid <= 0:
            raise AuthError('会员编号无效')
        if action == 'member-mfa-policy' and type(enabled) is not bool:
            raise AuthError('登录验证设置无效')
        if action == 'member-mfa-unbind' and (not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 200):
            raise AuthError('请输入解绑原因（1–200 字）')
        with self.auth.db.tx() as c:
            valid_session = c.execute(
                "SELECT 1 FROM web_sessions s JOIN web_users u ON u.id=s.user_id "
                "WHERE s.token_hash=? AND s.user_id=? AND s.scope='admin' AND s.mfa_verified=1 "
                "AND s.expires>? AND u.active=1 AND u.role='admin'",
                (session['token_hash'], actor['id'], int(time.time()))).fetchone()
            if not valid_session:
                raise AuthError('登录状态已变化，请重新登录', 401, 'mfa_expired')
            require_member_scope(c, actor, uid)
            user = c.execute("SELECT * FROM web_users WHERE id=? AND role='member'", (uid,)).fetchone()
            if not user:
                raise AuthError('会员不存在', 404)
            bound = bool(c.execute('SELECT 1 FROM web_totp_credentials WHERE user_id=?', (uid,)).fetchone())
            old = bool(user['member_mfa_required'])
            if action == 'member-mfa-policy':
                if old != enabled:
                    c.execute('UPDATE web_users SET member_mfa_required=? WHERE id=?', (enabled, uid))
                    self.auth.audit(c, actor['id'], 'member_mfa_policy', uid, ip, {'before': old, 'after': enabled})
                return {'ok': True}
            if not bound:
                raise AuthError('该会员尚未绑定验证器', 409)
            if self.reauthenticate(c, actor['id'], body.get('code'), ip):
                self.revoke(c, uid, 'member_center')
                c.execute('DELETE FROM web_totp_credentials WHERE user_id=?', (uid,))
                c.execute('DELETE FROM web_mfa_recovery_codes WHERE user_id=?', (uid,))
                self.auth.audit(c, actor['id'], 'member_mfa_unbind', uid, ip,
                                {'before_bound': True, 'after_bound': False, 'required': old, 'reason': reason.strip()})
                return {'ok': True}
        raise AuthError('管理员动态码无效或已使用，请等待下一组动态码', 401, 'mfa_invalid')

    def local_recovery(self, username, reason):
        with self.auth.db.tx() as c:
            row = c.execute("SELECT id FROM web_users WHERE username=? AND role='admin' AND active=1", (username,)).fetchone()
            if not row or not c.execute('SELECT 1 FROM web_totp_credentials WHERE user_id=?', (row['id'],)).fetchone():
                raise AuthError('未找到已绑定的启用管理员')
            uid = row['id']
            code = secrets.token_hex(12)
            self.revoke(c, uid, 'admin')
            c.execute('DELETE FROM web_mfa_recovery_codes WHERE user_id=? AND expires IS NOT NULL', (uid,))
            c.execute('INSERT INTO web_mfa_recovery_codes VALUES(?,?,?)', (uid, digest(code), int(time.time()) + 600))
            self.auth.audit(c, None, 'admin_mfa_local_recovery', uid, 'local', {'reason': reason, 'operator': getpass.getuser()})
            return code
