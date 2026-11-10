"""Controlled cutover: pause/drain, private backup, watchdog restart, verify, resume."""
import json,sqlite3,time,urllib.request,shutil,hashlib
from pathlib import Path
from datetime import datetime,timezone
from contextlib import closing
root=Path(__file__).resolve().parent.parent
stamp=datetime.now().strftime('%Y%m%d-%H%M%S');cfg=json.loads((root/'config.json').read_text(encoding='utf-8-sig'));db=root/cfg['database']
def health():return json.load(urllib.request.urlopen('http://127.0.0.1:8028/api/health',timeout=3))
old=health();target=root/'data/private'/('multi-account-cutover-'+stamp);target.mkdir()
shutil.copy2(root/'config.json',target/'config.json')
for p in (root/'data/private').glob('*.dpapi'):shutil.copy2(p,target/p.name)
with closing(sqlite3.connect(db,timeout=30)) as c:
 c.execute('BEGIN IMMEDIATE');settings=json.loads(c.execute('SELECT settings FROM robot_config WHERE id=1').fetchone()[0]);was_enabled=bool(settings['enabled']);settings['enabled']=False;c.execute('UPDATE robot_config SET settings=? WHERE id=1',(json.dumps(settings),));c.commit()
try:
 deadline=time.monotonic()+25
 while True:
  with closing(sqlite3.connect(db)) as c:sending=c.execute("SELECT COUNT(*) FROM robot_outbox WHERE status='sending'").fetchone()[0]
  if not sending:break
  if time.monotonic()>deadline:raise RuntimeError('Still sending; no restart performed')
  time.sleep(.25)
 with closing(sqlite3.connect(db)) as c,closing(sqlite3.connect(target/'pc28.sqlite3')) as dest:
  c.backup(dest);old_events=c.execute('SELECT id,event_id,recipient_id,request,status FROM robot_outbox ORDER BY id').fetchall();slots=c.execute('SELECT * FROM robot_delivery_slots ORDER BY rowid').fetchall();platforms=c.execute('SELECT id,name,endpoint,enabled FROM robot_platforms ORDER BY id').fetchall()
except BaseException:
 with closing(sqlite3.connect(db,timeout=30)) as c:
  c.execute('BEGIN IMMEDIATE')
  settings=json.loads(c.execute('SELECT settings FROM robot_config WHERE id=1').fetchone()[0])
  settings['enabled']=was_enabled
  c.execute('UPDATE robot_config SET settings=? WHERE id=1',(json.dumps(settings),))
  c.commit()
 raise
req=urllib.request.Request('http://127.0.0.1:8028/api/shutdown',data=b'{}',headers={'Content-Type':'application/json','X-PC28-Control':(root/'data/local-control-token.txt').read_text().strip(),'X-PC28-Restart':'1'})
with urllib.request.urlopen(req,timeout=5) as response:assert response.status==200
print('Paused and drained; backup complete; restarting through watchdog',flush=True)
deadline=time.monotonic()+45
while time.monotonic()<deadline:
 try:
  current=health()
  if current['ok'] and current['pid']!=old['pid']:break
 except Exception:pass
 time.sleep(.5)
else:raise RuntimeError('Restart not healthy; dispatch remains paused')
with closing(sqlite3.connect(db,timeout=30)) as c:
 assert c.execute('SELECT id,name,endpoint,enabled FROM robot_platforms ORDER BY id').fetchall()==platforms
 assert c.execute('SELECT * FROM robot_delivery_slots ORDER BY rowid').fetchall()==slots
 rows={r[0]:r for r in c.execute('SELECT id,event_id,recipient_id,request,status FROM robot_outbox')}
 for r in old_events:
  assert rows[r[0]][:4]==r[:4],'Dispatch identity/snapshot changed'
  if r[4] not in ('pending','retry'):assert rows[r[0]][4]==r[4]
  else:assert rows[r[0]][4] in ('cancelled','expired'), 'Old pending task was not cancelled'
 counts={t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ['robot_accounts','robot_bots','robot_subscriptions','robot_binding_map','robot_account_conflicts']}
 assert not counts['robot_account_conflicts'],'Resolve migration conflicts before resume'
 c.execute('BEGIN IMMEDIATE');settings=json.loads(c.execute('SELECT settings FROM robot_config WHERE id=1').fetchone()[0]);assert not settings['enabled'];settings['enabled']=was_enabled;c.execute('UPDATE robot_config SET settings=? WHERE id=1',(json.dumps(settings),));c.execute("INSERT INTO web_auth_events(created,actor,action,target,ip,details) VALUES(?,NULL,'robot_multi_account_deploy','normalized-v1','127.0.0.1',?)",(datetime.now(timezone.utc).isoformat(),json.dumps({'backup_dir':str(target),'counts':counts,'push_resumed':was_enabled})));c.commit()
report={'backup_dir':str(target),'old_pid':old['pid'],'new_pid':current['pid'],'historical_events_preserved':len(old_events),'old_dedup_slots_preserved':len(slots),'counts':counts,'push_resumed':was_enabled,'manual_real_signals_sent':0}
(root/'data/audits/multi-account-deployment.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(report,ensure_ascii=False),flush=True)
