"""Transactional index upgrade; preserve historical order IDs and all columns."""
import re


def migrate(c,table):
 if table not in ('custom_orders','strategy_signals'):raise ValueError('Invalid order table')
 old=c.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?',('table',table)).fetchone()
 if not old:return
 pattern=r'UNIQUE\s*\(\s*strategy\s*,\s*target_draw\s*\)'
 if not re.search(pattern,old[0],re.I):return
 # Refuse cascading rebuilds rather than silently modifying unrelated records.
 for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
  if any(fk[2]==table for fk in c.execute('PRAGMA foreign_key_list("'+row[0].replace('"','""')+'")')):
   raise ValueError('Order index migration requires review of incoming foreign keys')
 temporary=table+'__direction_v2'
 schema=re.sub(pattern,'UNIQUE(strategy,target_draw,direction)',old[0],flags=re.I)
 schema=re.sub(r'(CREATE TABLE\s+(?:IF NOT EXISTS\s+)?)"?'+table+r'"?',lambda m:m[1]+'"'+temporary+'"',schema,count=1,flags=re.I)
 objects=[row[0] for row in c.execute("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",(table,))]
 columns=','.join('"'+row[1].replace('"','""')+'"' for row in c.execute('PRAGMA table_info('+table+')'))
 count=c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
 c.execute(schema)
 c.execute('INSERT INTO '+temporary+'('+columns+') SELECT '+columns+' FROM '+table)
 if c.execute('SELECT COUNT(*) FROM '+temporary).fetchone()[0]!=count:raise ValueError('Order migration count mismatch')
 c.execute('DROP TABLE '+table)
 c.execute('ALTER TABLE '+temporary+' RENAME TO '+table)
 for sql in objects:c.execute(sql)
