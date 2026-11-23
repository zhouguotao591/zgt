"""Isolated historical replay; never constructs a live Service or starts robots."""
import json,os,subprocess,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
from .db import DB

def run_isolated(service,prepared,on_progress=None):
 folder=service.root/'data'/'backtest-work';folder.mkdir(parents=True,exist_ok=True)
 with tempfile.TemporaryDirectory(dir=folder) as work:
  request=Path(work)/'request.json';response=Path(work)/'response.json'
  request.write_text(json.dumps({'root':str(service.root.resolve()),'database':str(service.db.path.resolve()),'prepared':prepared},ensure_ascii=False),encoding='utf8')
  flags=subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name=='nt' else 0
  with (Path(work)/'stderr.log').open('w+',encoding='utf8') as errors:
   child=subprocess.Popen([sys.executable,'-m','pc28.history_worker',str(request),str(response)],cwd=Path(__file__).resolve().parent.parent,stdout=subprocess.PIPE,stderr=errors,text=True,encoding='utf8',creationflags=flags)
   try:
    for line in child.stdout:
     event=json.loads(line)
     if on_progress:on_progress(event['done'],event['total'])
    if child.wait()!=0:
     errors.seek(0);raise RuntimeError('独立回测进程失败：'+errors.read()[-2000:])
    value=json.loads(response.read_text(encoding='utf8'))
    if 'error' in value:raise ValueError(value['error'])
    return value['result']
   finally:
    if child.poll() is None:child.terminate();child.wait()
    child.stdout.close()

def main():
 from .history_backtest import execute
 request,response=map(Path,sys.argv[1:]);args=json.loads(request.read_text(encoding='utf8'))
 db=DB.__new__(DB);db.path=Path(args['database'])
 service=SimpleNamespace(root=Path(args['root']),db=db)
 def progress(done,total):print(json.dumps({'done':done,'total':total}),flush=True)
 try:
  result=execute(service,args['prepared'],progress)
  result['execution']={'isolation':'process','worker_pid':os.getpid()}
  value={'result':result}
 except Exception as e:value={'error':str(e)}
 response.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')

if __name__=='__main__':main()
