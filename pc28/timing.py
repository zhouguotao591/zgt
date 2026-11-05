"""Bounded draw-time triggers, independent of the periodic poll clock."""
from datetime import datetime,timezone,timedelta
import json,time

class DrawTimer:
 OFFSETS=(0,3,8,15)
 def __init__(self,state=None):self.state=state or {}
 def update(self,plan,latest_id,now=None):
  now=time.time() if now is None else now
  if self.state and latest_id>=self.state.get('next_draw_id',0):self.state={}
  if not plan:return
  target=plan['next_draw_id'];deadline=datetime.fromisoformat(plan['next_draw_at']).timestamp()
  if target<=latest_id or deadline<now-60 or deadline>now+172800:return
  attempts=self.state.get('attempts',0) if target==self.state.get('next_draw_id') and self.state.get('next_draw_at')==plan['next_draw_at'] else 0
  self.state={**plan,'attempts':attempts}
 def due(self,now):
  if not self.state:return False
  n=self.state.get('attempts',0)
  return n<len(self.OFFSETS) and now>=datetime.fromisoformat(self.state['next_draw_at']).timestamp()+self.OFFSETS[n]
 def mark(self):self.state['attempts']=self.state.get('attempts',0)+1

def extract_schedule(body,latest_id,now=None):
 now=time.time() if now is None else now
 try:
  p=json.loads(body)
  if not isinstance(p,dict):return None
  data=p.get('data',p)
  if not isinstance(data,dict):return None
  countdown=data.get('countdown',{});target=int(data.get('next_nbr',data.get('nextDrawNbr')));at=countdown.get('next_draw_at')
  if at is None and data.get('nextDrawAt'):
   parsed=datetime.fromisoformat(data['nextDrawAt'].replace('Z','+00:00'))
   if parsed.tzinfo is None:return None
   at=parsed.timestamp()
  if at is not None:
   at=float(at);at=at/1000 if at>100000000000 else at
  else:
   generated=datetime.fromisoformat(p['meta']['generated_at']);at=(generated+timedelta(seconds=float(countdown['seconds']))).timestamp()
  if target<=latest_id or not now-60<=at<=now+172800:return None
  return {'next_draw_id':target,'next_draw_at':datetime.fromtimestamp(at,timezone.utc).isoformat(timespec='seconds'),'resting':bool(countdown.get('resting',False))}
 except (ValueError,KeyError,TypeError,OverflowError):return None


def infer_break(db,now=None):
 """Forecast only repeatedly observed contiguous-ID publication gaps, never results."""
 from collections import defaultdict
 now=datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now,timezone.utc) if isinstance(now,(int,float)) else now
 rows=db.query('SELECT id,draw_utc FROM draws ORDER BY id DESC LIMIT 6000')[::-1]
 if not rows:return None
 try:latest=datetime.fromisoformat(rows[-1]['draw_utc'])
 except (TypeError,ValueError):return None
 if not latest.tzinfo or now<latest+timedelta(seconds=210):return None
 patterns=defaultdict(set)
 for a,b in zip(rows,rows[1:]):
  try:ta=datetime.fromisoformat(a['draw_utc']);tb=datetime.fromisoformat(b['draw_utc'])
  except (TypeError,ValueError):continue
  if not ta.tzinfo or not tb.tzinfo or b['id']!=a['id']+1:continue
  gap=(tb-ta).total_seconds()
  if 600<gap<=21600 and ta.astimezone(timezone.utc).time()==latest.astimezone(timezone.utc).time():patterns[(tb.astimezone(timezone.utc).time(),int(gap))].add(ta.date())
 if not patterns:return None
 (clock,gap),dates=max(patterns.items(),key=lambda item:len(item[1]))
 if len(dates)<3:return None
 at=latest+timedelta(seconds=gap)
 if now>at+timedelta(minutes=15):return None
 return {'next_draw_id':rows[-1]['id']+1,'next_draw_at':at.astimezone(timezone.utc).isoformat(timespec='seconds'),'resting':now<at,'inferred':True,'evidence_days':len(dates),'source':'local_history_pattern'}

def publication_state(latest,plan,now=None):
 now=now or datetime.now(timezone.utc)
 if not latest or not latest.get('draw_utc'):return {'phase':'empty','message':'等待获取有效开奖数据'}
 at=datetime.fromisoformat(latest['draw_utc']);age=(now-at).total_seconds();expected=at+timedelta(seconds=210)
 from .maintenance import window
 rest_start,rest_end=window(now)
 if rest_start<=now<rest_end:
  from zoneinfo import ZoneInfo
  until=rest_end.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')
  return {'phase':'rest','message':'暂停休息中：每日太平洋时间04:00—04:30暂停，预计北京时间'+until+'结束；后台继续查询，暂停前末期及暂停期间不新开单','expected_next_at':rest_end.isoformat(),'rest_end_at':rest_end.isoformat(),'rest_start_at':rest_start.isoformat(),'inferred':False,'seconds_since_last':max(0,int(age)),'delay_seconds':0}
 if plan and plan.get('next_draw_id')==latest['id']+1:
  try:expected=datetime.fromisoformat(plan['next_draw_at'])
  except (ValueError,KeyError):pass
 inferred=bool(plan and plan.get('inferred'));resting=bool(plan and plan.get('resting'))
 phase='break' if (inferred or resting) and now<expected else 'live' if age<=240 else 'awaiting' if now<=expected+timedelta(seconds=60) else 'delayed'
 from zoneinfo import ZoneInfo
 time_text=expected.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%H:%M:%S')
 message=('历史日常开奖间隔，预计北京时间'+time_text+'恢复（按'+str(plan.get('evidence_days',0))+'天连续期号间隔推算，待上游确认）') if phase=='break' and inferred else ('上游通知开奖间隔，预计'+time_text+'恢复') if phase=='break' else '已到预计开奖时间，等待上游发布结果' if phase=='awaiting' else f"上游暂未更新，距上期已{max(0,int(age//60))}分钟；每30秒继续查询" if phase=='delayed' else ''
 return {'phase':phase,'message':message,'expected_next_at':expected.isoformat(),'inferred':inferred,'evidence_days':plan.get('evidence_days',0) if plan else 0,'seconds_since_last':max(0,int(age)),'delay_seconds':max(0,int((now-expected).total_seconds()))}
