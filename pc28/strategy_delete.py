"""Archive strategy definitions without removing financial history."""
from .model import utcnow

def deleted_ids(db):
 return {r['id'] for r in db.query('SELECT id FROM deleted_strategies')}

def require_available(db,key):
 if key and key in deleted_ids(db):raise ValueError('该策略已删除')

def delete(service,body):
 from .strategies import catalog
 key=body.get('id')
 with service.lock:
  require_available(service.db,key)
  row=next((r for r in catalog(service.db,service.cfg)['strategies'] if r['id']==key),None)
  if not row:raise ValueError('策略不存在')
  if row['enabled']:raise ValueError('请先停用策略，再删除')
  with service.db.tx() as c:
   if key=='legacy':pending=c.execute("SELECT 1 FROM orders WHERE status='pending'").fetchone()
   elif key in ('size','parity'):pending=c.execute("SELECT 1 FROM trend_orders WHERE strategy=? AND status='pending'",(key,)).fetchone()
   else:pending=c.execute("SELECT 1 FROM custom_orders WHERE strategy=? AND status='pending'",(key,)).fetchone()
   if pending:raise ValueError('请等待已有订单结算后再删除')
   c.execute('INSERT INTO deleted_strategies(id,name,deleted_at) VALUES(?,?,?)',(key,row['name'],utcnow()))
 return {'ok':True,'id':key}
