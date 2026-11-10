"""PlayNow daily 04:00–04:30 Pacific break; BC stays UTC-7 after March 2026.
Sources: https://www.playnow.com/about-playnow/help.html
https://news.gov.bc.ca/releases/2026AG0013-000209
This calendar is independent of legacy CSV/source timezone conversion.
"""
from datetime import datetime,timedelta,timezone,time
from zoneinfo import ZoneInfo
import json
BC_PERMANENT=datetime(2026,3,8,10,tzinfo=timezone.utc)
def bc_zone(at):
 return timezone(timedelta(hours=-7),'BC Pacific') if at>=BC_PERMANENT else ZoneInfo('America/Vancouver')
def window(at):
 local=at.astimezone(bc_zone(at));zone=local.tzinfo
 start=datetime.combine(local.date(),time(4),zone);end=datetime.combine(local.date(),time(4,30),zone)
 return start.astimezone(timezone.utc),end.astimezone(timezone.utc)
def last_slot(at):
 start,_=window(at);return start-timedelta(seconds=210)
def is_last_slot(at):return abs((at-last_slot(at)).total_seconds())<=30
def block_reason(latest,now,enabled=True,planned=None):
 if not enabled:return ''
 start,end=window(now)
 if start<=now<end:return '暂停休息中，暂停期间不新开单'
 try:last=datetime.fromisoformat(latest['draw_utc'])
 except (KeyError,ValueError,TypeError):return ''
 if not last.tzinfo:return ''
 if is_last_slot(last):return '暂停前最后一期已出，休息结束并收到新结果后再评估'
 target=planned or last+timedelta(seconds=210)
 if is_last_slot(target) or is_last_slot(last+timedelta(seconds=210)):return '暂停前最后一期不下单'
 target_start,target_end=window(target)
 if target_start<=target<target_end:return '目标期位于暂停休息时段，不下单'
 return ''
def live_guard(c,latest,now,cfg):
 planned=None;row=c.execute("SELECT value FROM runtime_state WHERE key='draw_timer'").fetchone()
 if row:
  try:
   data=json.loads(row[0])
   if data.get('next_draw_id')==latest['id']+1:planned=datetime.fromisoformat(data['next_draw_at'])
  except (KeyError,TypeError,ValueError):pass
 return block_reason(latest,now,cfg['collector'].get('maintenance_guard_enabled',True),planned)

def covered_gap(before,after):
 start,end=window(before+timedelta(minutes=5))
 return before<=start<end<=after and 0<=(start-before).total_seconds()<=240 and 0<=(after-end).total_seconds()<=240
def hour_start_covered(begin,first):
 start,end=window(begin)
 return (first-begin).total_seconds()<=240 or start==begin and 0<=(first-end).total_seconds()<=240
