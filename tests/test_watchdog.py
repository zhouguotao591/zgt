import json,os,socket,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
from pc28.watchdog import Watchdog,InstanceLock
REPO=Path(__file__).resolve().parent.parent

class WatchdogTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=REPO/'data');self.root=Path(self.tmp.name)
  with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
  (self.root/'config.json').write_text(json.dumps({'port':port}));self.command=[sys.executable,'-u',str(REPO/'tests/watchdog_child.py'),str(self.root)]
  self.settings={'interval_seconds':.1,'health_timeout_seconds':.2,'failure_threshold':2,'startup_grace_seconds':.3,'max_restart_delay_seconds':.2,'stable_seconds':100}
  (self.root/'data').mkdir();(self.root/'data/local-control-token.txt').write_text('fixture-only');self.w=None;self.thread=None
 def tearDown(self):
  if self.w:
   self.w.stop_file.write_text('stop')
   if self.thread:self.thread.join(12)
   self.w.stop_child()
  self.tmp.cleanup()
 def start(self,mode):
  self.w=Watchdog(self.root,self.settings,self.command+[mode]);self.thread=threading.Thread(target=self.w.run,daemon=True);self.thread.start()
 def await_state(self,predicate,timeout=12):
  end=time.monotonic()+timeout
  while time.monotonic()<end:
   try:
    state=json.loads((self.root/'data/watchdog-state.json').read_text())
    if predicate(state):return state
   except (OSError,ValueError):pass
   time.sleep(.05)
  self.fail('watchdog state timed out')
 def test_exit_restarts_with_original_error_and_exit_code(self):
  self.start('crash_once');state=self.await_state(lambda s:s['status']=='running' and s['restart_count']>=1)
  reports=[json.loads(p.read_text()) for p in (self.root/'data/logs').glob('failure-*.json')];self.assertEqual(reports[0]['exit_code'],17);self.assertIn('simulated traceback',reports[0]['stderr_tail']);self.assertEqual(int((self.root/'launch-count.txt').read_text()),2)
  self.assertTrue(Path(state['stderr_log']).exists())
 def test_unhealthy_process_restarts_after_consecutive_failures(self):
  self.start('unhealthy_once');self.await_state(lambda s:s['status']=='running' and s['restart_count']>=1)
  reports=[json.loads(p.read_text()) for p in (self.root/'data/logs').glob('failure-*.json')];self.assertEqual(reports[0]['reason'],'健康检查连续失败')
 def test_intentional_stop_does_not_restart_and_lock_is_released(self):
  self.start('healthy');self.await_state(lambda s:s['status']=='running');self.w.stop_file.write_text('stop');self.thread.join(12);self.assertFalse(self.thread.is_alive());self.assertEqual(int((self.root/'launch-count.txt').read_text()),1);self.assertEqual(json.loads(self.w.state_file.read_text())['status'],'stopped')
  lock=InstanceLock(self.root/'data/private/watchdog.lock');self.assertTrue(lock.acquire());lock.close()
 def test_single_instance_lock_and_foreign_service_identity(self):
  first=InstanceLock(self.root/'lock');second=InstanceLock(self.root/'lock');self.assertTrue(first.acquire());self.assertFalse(second.acquire());first.close()
  self.start('foreign');self.await_state(lambda s:s['status']=='starting');data,error=self.w.probe();self.assertIsNone(data)
 @unittest.skipUnless(os.name=='nt','Windows process-handle adoption')
 def test_existing_healthy_service_is_adopted_without_duplicate(self):
  process=subprocess.Popen(self.command+['healthy'],cwd=self.root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  self.addCleanup(lambda:process.poll() is None and process.kill());w=Watchdog(self.root,self.settings,self.command+['healthy']);end=time.monotonic()+5
  while time.monotonic()<end:
   if w.probe()[0]:break
   time.sleep(.05)
  self.w=w;self.thread=threading.Thread(target=w.run,daemon=True);self.thread.start();state=self.await_state(lambda s:s['status']=='running');self.assertEqual(state['service_pid'],process.pid);self.assertEqual(int((self.root/'launch-count.txt').read_text()),1)

if __name__=='__main__':unittest.main()
