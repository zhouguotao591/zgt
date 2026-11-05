"""Persistent pause intervals use draw IDs, independent of received/missing rows."""
def validate(limit,draws):
 if type(limit) is not int or not 0<=limit<=1000:raise ValueError('连续盈利暂停次数须为0—1000，0表示关闭')
 if type(draws) is not int or not 1<=draws<=10000:raise ValueError('暂停期数须为1—10000')
def settled(state,outcome,target,limit=5,draws=20):
 state['win_streak']=state.get('win_streak',0)+1 if outcome=='win' else 0
 if limit and state['win_streak']>=limit:state['pause_until']=target+draws
 return state
def paused(state,target):
 until=state.get('pause_until',0)
 if until and target<=until:return f'连胜暂停：至{until}期，当前剩余{until-target+1}期不下单'
 if until:state.update(pause_until=0,win_streak=0)
 return ''
