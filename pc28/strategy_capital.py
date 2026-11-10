"""Apply capital changes without rewriting past strategy results."""
from .model import money


def sync_capital(c,key,capital):
 if key=='legacy':
  account,column,value='accounts','id',1
 elif key in ('size','parity'):
  account,column,value='trend_accounts','strategy',key
 else:
  account,column,value='custom_strategies','id',key
 initial=money(capital)
 c.execute(f'''UPDATE {account}
  SET balance=balance+(?-initial_balance),peak=peak+(?-initial_balance),initial_balance=?
  WHERE {column}=? AND initial_balance!=?''',(initial,initial,initial,value,initial))
