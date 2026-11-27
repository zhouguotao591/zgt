"""Robot-batch member orders; existing member_orders remain immutable line items."""
import json

TABLE='member_robot_orders'
KEY="""CASE WHEN COALESCE(o.batch_id,'')<>'' OR o.bot_id IS NOT NULL
 THEN printf('robot:%d:%d:%d:%d:%d:%s',o.user_id,COALESCE(o.platform_id,-1),COALESCE(o.follow_account_id,-1),COALESCE(o.bot_id,-1),o.target_draw,COALESCE(o.batch_id,''))
 ELSE 'legacy:'||o.id END"""


def initialize(c):
 c.execute('CREATE VIEW IF NOT EXISTS member_order_items AS SELECT o.*,'+KEY+' AS order_key FROM member_orders o')
 c.execute('''CREATE VIEW IF NOT EXISTS member_robot_orders AS
 WITH totals AS (
 SELECT MIN(id) id,user_id,order_key,MAX(target_draw) target_draw,
 MAX(platform_id) platform_id,MAX(follow_account_id) follow_account_id,MAX(bot_id) bot_id,
 MAX(batch_id) batch_id,GROUP_CONCAT(DISTINCT strategy_name) strategy_name,GROUP_CONCAT(DISTINCT direction) direction,
 SUM(stake) stake,SUM(COALESCE(original_stake,stake)) original_stake,
 MIN(placed_at) placed_at,MAX(accepted_at) accepted_at,MAX(draw_time) draw_time,MAX(settled_at) settled_at,
 MAX(result) result,MAX(total) total,COUNT(*) detail_count,
 SUM(status='pending') pending_count,SUM(status='awaiting_result') awaiting_count,SUM(status='settled') settled_count,
 SUM(outcome='offset') offset_count,SUM(status='settled' AND outcome<>'refund') nonrefund_count,
 COALESCE(SUM(CASE WHEN status='settled' THEN pnl END),0) settled_pnl,
 COALESCE(SUM(CASE WHEN status='settled' THEN stake END),0) settled_stake,
 COALESCE(SUM(CASE WHEN status IN ('pending','awaiting_result') THEN stake END),0) pending_stake
 FROM member_order_items GROUP BY user_id,order_key)
 SELECT totals.*,
 CASE WHEN pending_count>0 THEN 'pending' WHEN awaiting_count>0 THEN 'awaiting_result' WHEN settled_count>0 THEN 'settled' ELSE 'void' END status,
 CASE WHEN pending_count+awaiting_count>0 THEN NULL ELSE settled_pnl END pnl,
 CASE WHEN pending_count+awaiting_count>0 THEN NULL
 WHEN offset_count=detail_count THEN 'offset' WHEN settled_count=0 THEN 'void'
 WHEN settled_pnl>0 THEN 'win' WHEN settled_pnl<0 THEN 'loss'
 WHEN nonrefund_count=0 THEN 'refund' ELSE 'flat' END outcome
 FROM totals''')


def _message(items,original=False):
 totals={}
 for item in items:
  amount=(item.get('original_stake') if item.get('original_stake') is not None else item['stake']) if original else item['stake']
  if amount:totals[item['direction']]=totals.get(item['direction'],0)+amount
 return ' '.join(direction+format(amount/100,'.2f').rstrip('0').rstrip('.') for direction,amount in totals.items())


def hydrate(c,row,details=True):
 result=dict(row)
 items=[dict(r) for r in c.execute('''SELECT o.*,
 (SELECT before_balance FROM member_cash_ledger l WHERE l.order_id=o.id AND l.kind='stake') before_balance,
 (SELECT after_balance FROM member_cash_ledger l WHERE l.order_id=o.id AND l.kind='stake') after_stake_balance,
 (SELECT SUM(amount) FROM member_cash_ledger l WHERE l.order_id=o.id AND l.kind IN ('settle','execution_reversal')) credit
 FROM member_order_items o WHERE user_id=? AND order_key=? ORDER BY id''',(result['user_id'],result['order_key']))]
 result['message']=_message(items);result['original_message']=_message(items,True)
 ids=[item['id'] for item in items]
 ledger=[dict(r) for r in c.execute('SELECT * FROM member_cash_ledger WHERE order_id IN ('+','.join('?' for _ in ids)+') ORDER BY id',ids)]
 stakes=[r for r in ledger if r['kind']=='stake'];settles=[r for r in ledger if r['kind'] in ('settle','execution_reversal')]
 result.update(before_balance=stakes[0]['before_balance'] if stakes else None,after_stake_balance=stakes[-1]['after_balance'] if stakes else None,
               before_settle_balance=settles[0]['before_balance'] if settles else None,after_settle_balance=settles[-1]['after_balance'] if settles else None,
               settlement_credit=sum(r['amount'] for r in settles) if settles else None,credit=sum(r['amount'] for r in settles) if settles else None,
               debit=sum(-r['amount'] for r in stakes),account_name=result.get('account_name','历史账号'),bot_name=result.get('bot_name','历史机器人'),platform_name=result.get('platform_name','历史平台'),merged=True)
 for table,key,label in [('robot_accounts','follow_account_id','account_name'),('robot_bots','bot_id','bot_name'),('robot_platforms','platform_id','platform_name')]:
  name=c.execute('SELECT name FROM '+table+' WHERE id=?',(result[key],)).fetchone()
  if name:result[label]=name[0]
 draw=c.execute('SELECT a,b,c,size,parity FROM draws WHERE id=?',(result['target_draw'],)).fetchone()
 for field in ('a','b','c'):
  result['draw_'+field]=draw[field] if draw else None;result[field]=draw[field] if draw else None
 if draw and result['total'] is not None:result['result']=draw['size']+draw['parity']
 for item in items:
  odds=json.loads(item.pop('odds_json'))
  item['odds_factor']=None
  if item['status']=='settled' and item['stake']:
   from .model import settlement
   paid=settlement(item['stake'],item['direction'],item['total'],item['outcome'] in ('win','refund'),odds)
   item['odds_factor']=paid['factor']
 if details:result['items']=items
 result['odds_factor']=items[0]['odds_factor'] if len(items)==1 else None
 return result


def member_detail(c,uid,order_id):
 item=c.execute('SELECT order_key FROM member_order_items WHERE id=? AND user_id=?',(order_id,uid)).fetchone()
 if not item:return None
 row=c.execute('SELECT * FROM member_robot_orders WHERE user_id=? AND order_key=?',(uid,item['order_key'])).fetchone()
 return hydrate(c,row) if row else None
