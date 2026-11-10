"""Persistent pause intervals use draw IDs, independent of received/missing rows."""
def validate(limit,draws,difference_limit=0):
 if type(limit) is not int or not 0<=limit<=1000:raise ValueError('连续盈利暂停次数须为0—1000，0表示关闭')
 if type(draws) is not int or not 1<=draws<=10000:raise ValueError('暂停期数须为1—10000')
 if type(difference_limit) is not int or not 0<=difference_limit<=100000:raise ValueError('胜负差值暂停单数须为0—100000，0表示关闭')
def settled(state,outcome,target,limit=5,draws=20,difference_limit=0):
 state['win_streak']=state.get('win_streak',0)+1 if outcome=='win' else 0
 state['win_loss_difference']=state.get('win_loss_difference',0)+(1 if outcome=='win' else -1 if outcome=='loss' else 0)
 if difference_limit and abs(state['win_loss_difference'])>=difference_limit:state['difference_paused']=True
 if limit and state['win_streak']>=limit:state['pause_until']=target+draws
 return state
def paused(state,target):
 if state.get('difference_paused'):return f"胜负差值暂停：当前{state.get('win_loss_difference',0):+d}单，需手动重新启用策略"
 until=state.get('pause_until',0)
 if until and target<=until:return f'连胜暂停：至{until}期，当前剩余{until-target+1}期不下单'
 if until:state.update(pause_until=0,win_streak=0)
 return ''

def restart(state):
 """Restart a halted strategy without leaving a simultaneous timed pause behind."""
 if state.get('difference_paused'):state.update(win_streak=0,pause_until=0)
 state.update(win_loss_difference=0,difference_paused=False)
 return state

def difference_rule(settings):
 limit=settings.get('win_loss_difference_limit',0)
 return f'；胜负差值绝对值达到{limit}单一直暂停，手动重新启用清零' if limit else ''
