"""Independent service supervisor: no database, strategy or order writes."""
import ctypes,hashlib,json,logging,logging.handlers,os,socket,subprocess,sys,time,uuid
from datetime import datetime,timezone
from pathlib import Path
import urllib.request

DEFAULTS={'interval_seconds':10,'health_timeout_seconds':3,'failure_threshold':3,'startup_grace_seconds':90,'max_restart_delay_seconds':60,'stable_seconds':300}
def root_id(root):return hashlib.sha256(str(Path(root).resolve()).casefold().encode()).hexdigest()[:24]
def now():return datetime.now(timezone.utc).isoformat()
def write_json(path,value):
 temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temporary.replace(path)

class InstanceLock:
 def __init__(self,path):self.path=path;self.file=None
 def acquire(self):
  self.path.parent.mkdir(parents=True,exist_ok=True);self.file=self.path.open('a+b');self.file.seek(0,2)
  if not self.file.tell():self.file.write(b'0');self.file.flush()
  self.file.seek(0)
  try:
   if os.name=='nt':
    import msvcrt
    msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
   else:
    import fcntl
    fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
   return True
  except OSError:self.file.close();self.file=None;return False
 def close(self):
  if self.file:self.file.close();self.file=None

class WindowsProcess:
 """Hold a Windows process handle, so PID reuse cannot kill another process."""
 def __init__(self,pid):
  from ctypes import wintypes
  self.pid=pid;self.k=ctypes.WinDLL('kernel32',use_last_error=True)
  self.k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];self.k.OpenProcess.restype=wintypes.HANDLE
  self.k.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];self.k.WaitForSingleObject.restype=wintypes.DWORD
  self.k.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
  self.k.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT];self.k.CloseHandle.argtypes=[wintypes.HANDLE]
  self.handle=self.k.OpenProcess(0x100000|0x1000|1,False,pid)
  if not self.handle:raise OSError('无法打开已核验的服务进程')
 def poll(self):
  from ctypes import wintypes
  if self.k.WaitForSingleObject(self.handle,0)==258:return None
  code=wintypes.DWORD();self.k.GetExitCodeProcess(self.handle,ctypes.byref(code));return code.value
 def terminate(self):self.k.TerminateProcess(self.handle,1)
 kill=terminate
 def wait(self,timeout):
  if self.k.WaitForSingleObject(self.handle,int(timeout*1000))==258:raise subprocess.TimeoutExpired('adopted-service',timeout)
  return self.poll()
 def close(self):
  if self.handle:self.k.CloseHandle(self.handle);self.handle=None

class Watchdog:
 def __init__(self,root,settings=None,command=None):
  self.root=Path(root).resolve();cfg=json.loads((self.root/'config.json').read_text(encoding='utf-8-sig'));self.port=int(cfg['port']);self.settings={**DEFAULTS,**cfg.get('watchdog',{}),**(settings or {})}
  for k,v in self.settings.items():
   if k not in DEFAULTS or isinstance(v,bool) or not isinstance(v,(int,float)) or not 0<v<=3600:raise ValueError('守护配置无效：'+k)
  self.logs=self.root/'data/logs';self.logs.mkdir(parents=True,exist_ok=True);self.stop_file=self.root/'data/watchdog.stop';self.state_file=self.root/'data/watchdog-state.json';self.lock=InstanceLock(self.root/'data/private/watchdog.lock')
  self.log=logging.getLogger('pc28-watchdog-'+uuid.uuid4().hex);self.log.setLevel(logging.INFO);self.log.propagate=False
  handler=logging.handlers.RotatingFileHandler(self.logs/'watchdog.log',maxBytes=2*1024*1024,backupCount=10,encoding='utf-8');handler.setFormatter(logging.Formatter('%(message)s'));self.log.addHandler(handler)
  self.command=command or [sys.executable,'-u','-X','utf8','-X','faulthandler','-m','pc28','--root',str(self.root),'serve'];self.child=None;self.stdout=None;self.stderr=None;self.restarts=0;self.failures=0;self.started=0;self.last_failure=None;self.ready=False
  self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 def event(self,event,**fields):self.log.info(json.dumps({'time':now(),'event':event,**fields},ensure_ascii=False))
 def state(self,status,**fields):
  try:write_json(self.state_file,{'status':status,'updated':now(),'watchdog_pid':os.getpid(),'service_pid':self.child.pid if self.child else None,'restart_count':self.restarts,'health_failures':self.failures,'last_failure':self.last_failure,'stdout_log':str(self.stdout) if self.stdout else None,'stderr_log':str(self.stderr) if self.stderr else None,**fields})
  except OSError:self.event('state_file_write_failed',status=status)
 def probe(self):
  try:
   with self.opener.open(f'http://127.0.0.1:{self.port}/api/health',timeout=self.settings['health_timeout_seconds']) as r:data=json.loads(r.read(2048))
   if data.get('service')!='pc28' or data.get('root_id')!=root_id(self.root) or data.get('ok') is not True or type(data.get('pid'))!=int:return None,'服务身份校验失败'
   if self.child and data['pid']!=self.child.pid:
    # Windows virtualenv launchers start the real Python interpreter as a child.
    if os.name!='nt' or data.get('parent_pid')!=self.child.pid:return None,'健康接口进程与受监控进程不一致'
   return data,''
  except Exception as e:return None,type(e).__name__
 def port_busy(self):
  try:
   with socket.create_connection(('127.0.0.1',self.port),timeout=1):return True
  except OSError:return False
 def launch(self):
  healthy,_=self.probe()
  if healthy:
   if os.name!='nt':raise RuntimeError('现有服务需先正常停止，再交由守护启动')
   self.child=WindowsProcess(healthy['pid']);self.event('service_adopted',pid=self.child.pid)
  else:
   if self.port_busy():raise RuntimeError('端口被未通过身份核验的服务占用，不启动重复进程')
   tag=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6];self.stdout=self.logs/('service-'+tag+'.stdout.log');self.stderr=self.logs/('service-'+tag+'.stderr.log')
   env={**os.environ,'PYTHONUNBUFFERED':'1','PYTHONFAULTHANDLER':'1'}
   with self.stdout.open('ab',buffering=0) as out,self.stderr.open('ab',buffering=0) as err:
    self.child=subprocess.Popen(self.command,cwd=self.root,env=env,stdout=out,stderr=err,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
   self.event('service_started',pid=self.child.pid,stdout=str(self.stdout),stderr=str(self.stderr))
  self.started=time.monotonic();self.failures=0;self.ready=False;self.state('starting')
 def crash_report(self,reason,exit_code=None):
  def tail(path):
   if not path or not path.exists():return ''
   with path.open('rb') as f:f.seek(max(0,path.stat().st_size-16000));return f.read().decode('utf-8',errors='replace')
  path=self.logs/('failure-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]+'.json')
  report={'time':now(),'reason':reason,'service_pid':self.child.pid if self.child else None,'exit_code':exit_code,'stdout_log':str(self.stdout) if self.stdout else None,'stderr_log':str(self.stderr) if self.stderr else None,'stderr_tail':tail(self.stderr),'stdout_tail':tail(self.stdout)}
  write_json(path,report);self.last_failure={'time':report['time'],'reason':reason,'exit_code':exit_code,'report':str(path)};self.event('service_failure',**self.last_failure);return path
 def graceful_shutdown(self):
  try:
   with self.opener.open(f'http://127.0.0.1:{self.port}/api/session',timeout=2) as r:session=json.loads(r.read())
   token=(self.root/'data/local-control-token.txt').read_text().strip();req=urllib.request.Request(f'http://127.0.0.1:{self.port}/api/shutdown',data=b'{}',headers={'X-PC28-Token':session['token'],'X-PC28-Control':token,'Content-Type':'application/json','X-PC28-Restart':'1'},method='POST')
   with self.opener.open(req,timeout=2):pass
  except Exception:pass
 def stop_child(self,graceful=False):
  if not self.child:return
  if self.child.poll() is None:
   if graceful:self.graceful_shutdown()
   else:self.child.terminate()
   try:self.child.wait(timeout=8 if graceful else 5)
   except subprocess.TimeoutExpired:self.child.kill();self.child.wait(timeout=5)
  if hasattr(self.child,'close'):self.child.close()
  self.child=None
 def wait(self,seconds):
  end=time.monotonic()+seconds
  while time.monotonic()<end:
   if self.stop_file.exists():return False
   time.sleep(min(.5,max(0,end-time.monotonic())))
  return not self.stop_file.exists()
 def retry_delay(self):return min(self.settings['max_restart_delay_seconds'],2**min(self.restarts,6))
 def run(self):
  if not self.lock.acquire():
   self.event('duplicate_watchdog_ignored')
   for h in list(self.log.handlers):h.close();self.log.removeHandler(h)
   return 0
  try:
   self.stop_file.unlink(missing_ok=True);self.event('watchdog_started',pid=os.getpid(),settings=self.settings);self.state('starting')
   while not self.stop_file.exists():
    if self.child is None:
     try:self.launch()
     except Exception as e:
      self.crash_report('启动失败：'+str(e));self.restarts+=1;delay=self.retry_delay();self.state('retrying',retry_after_seconds=delay)
      if not self.wait(delay):break
      continue
    code=self.child.poll()
    if code is not None:
     if self.stop_file.exists():break
     self.crash_report('服务进程已退出',code);self.stop_child();self.restarts+=1;delay=self.retry_delay();self.state('retrying',retry_after_seconds=delay)
     if not self.wait(delay):break
     continue
    data,error=self.probe();age=time.monotonic()-self.started
    if data:
     self.failures=0;self.ready=True
     if age>=self.settings['stable_seconds']:self.restarts=0
     self.state('running')
    elif self.ready or age>=self.settings['startup_grace_seconds']:
     self.failures+=1;self.event('health_check_failed',pid=self.child.pid,failure_count=self.failures,error=error);self.state('unhealthy')
     if self.failures>=self.settings['failure_threshold']:
      self.crash_report('健康检查连续失败');self.stop_child(graceful=True);self.restarts+=1;delay=self.retry_delay();self.state('retrying',retry_after_seconds=delay)
      if not self.wait(delay):break
      continue
    if not self.wait(self.settings['interval_seconds']):break
   self.event('intentional_stop_requested');self.stop_child(graceful=True);self.state('stopped');return 0
  except KeyboardInterrupt:
   self.stop_child(graceful=True);self.state('stopped');return 0
  except BaseException:
   self.log.exception(json.dumps({'time':now(),'event':'watchdog_unhandled_error'}));raise
  finally:
   self.lock.close()
   for h in list(self.log.handlers):h.close();self.log.removeHandler(h)

def main(root):return Watchdog(root).run()
