import sys,json,copy,shutil,tempfile
from pathlib import Path
from unittest.mock import patch
from datetime import datetime,timezone,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pc28.service import Service
from pc28.config import DEFAULT
from pc28.server import LocalServer
with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent/'data') as folder:
 root=Path(folder);cfg=copy.deepcopy(DEFAULT);cfg['port']=18031;cfg['collector']['enabled']=False;cfg['collector']['maintenance_guard_enabled']=False;(root/'config.json').write_text(json.dumps(cfg));shutil.copytree(Path(__file__).resolve().parent.parent/'web',root/'web');s=Service(root);now=datetime.now(timezone.utc)
 s.db.import_records([{'id':100+i,'time':(now-timedelta(seconds=210*(89-i))).isoformat(),'numbers':list(range(1,21))} for i in range(90)],'test')
 original=s.auth.captcha
 def fixture_captcha(session,ip):
  with patch('pc28.auth.secrets.randbelow',return_value=1):return original(session,ip)
 s.auth.captcha=fixture_captcha
 state={'root':str(root),'setup_code':(root/'data/admin-setup-code.txt').read_text(),'control_token':(root/'data/local-control-token.txt').read_text()};(Path(__file__).resolve().parent.parent/'data/auth-ui-test-state.json').write_text(json.dumps(state))
 server=LocalServer(s);s.start();print('READY auth isolated fixture 18031',flush=True)
 try:server.serve_forever(poll_interval=.1)
 finally:s.stop.set();server.server_close();s.worker.join(2);s.collect_worker.join(2);s.scheduler.join(2)
