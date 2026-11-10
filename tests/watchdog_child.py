"""Disposable HTTP child for supervisor tests; never touches the real service."""
import sys,json,os,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from pc28.watchdog import root_id
root=Path(sys.argv[1]);mode=sys.argv[2];counter=root/'launch-count.txt';count=int(counter.read_text())+1 if counter.exists() else 1;counter.write_text(str(count))
if mode=='crash_once' and count==1:
 print('simulated traceback: fixture crash',file=sys.stderr,flush=True);raise SystemExit(17)
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,code,value):
  body=json.dumps(value).encode();self.send_response(code);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def do_GET(self):
  if self.path=='/api/health':self.send(503 if mode=='unhealthy_once' and count==1 else 200,{'ok':True,'service':'pc28','pid':os.getpid(),'root_id':root_id(root) if mode!='foreign' else 'foreign'});return
  self.send(200,{'token':'fixture-csrf'})
 def do_POST(self):
  self.send(200,{'ok':True});threading.Thread(target=self.server.shutdown,daemon=True).start()
cfg=json.loads((root/'config.json').read_text());server=ThreadingHTTPServer(('127.0.0.1',cfg['port']),Handler);server.serve_forever(poll_interval=.05);server.server_close()
