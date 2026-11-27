"""Locked opposite-combination rounds with integer-cent amount progression."""
OPPOSITE={'大双':'小单','小单':'大双','大单':'小双','小双':'大单'}
FILTER_DEFAULTS={'confirmation_filter':False,'trend_filter':False,'trend_lookback':20,'trend_threshold_percent':70,'entry_combos':['大双','大单','小双','小单'],'round_stop_cents':0,'prior_opposite_draws':0}


def prior_opposite_allows(history,settings,source):
    count=settings.get('prior_opposite_draws',0)
    if not count:return True,''
    # Anchor to the beginning of the WHOLE current run, not a sliding threshold tail.
    start=len(history)-1
    while start>0 and history[start-1]['id']+1==history[start]['id'] and history[start-1]['size']+history[start-1]['parity']==source:
        start-=1
    before=history[max(0,start-count):start]
    chain=before+history[start:]
    if len(before)<count or any(b['id']!=a['id']+1 for a,b in zip(chain,chain[1:])) or any(r.get('verification') not in ('full_keno','crosschecked_abc') for r in chain):
        return False,'连续组合之前的观察窗口不足、缺期或未核验'
    opposite=OPPOSITE[source]
    if any(r['size']+r['parity']==opposite for r in before):
        return False,f'连续{source}开始前{count}期内出现过{opposite}，跳过入场'
    return True,''


def end_on_amount(state,settings,stake,cutoff_draw):
    limit=settings.get('round_stop_cents',0)
    if not limit or stake<limit:return False
    state.update(combo_active=False,combo_observe_after=cutoff_draw,attempt=0,losses=0,
                 combo_loss_count=0,next_stake=settings['base_cents'])
    state.pop('combo_pending',None)
    state.pop('combo_direction',None)
    return True


def validate_stages(stages):
    if stages is None:return
    if not isinstance(stages,list) or len(stages)>100:raise ValueError('亏后加额阶段最多100段')
    end=0
    for stage in sorted(stages,key=lambda x:x.get('start',0) if isinstance(x,dict) else 0):
        if not isinstance(stage,dict) or set(stage)!={'start','end','mode','value'}:raise ValueError('加额阶段字段不完整')
        if any(type(stage[k]) is not int for k in ('start','end','value')):raise ValueError('阶段期数及金额分/比例必须为整数')
        if not 1<=stage['start']<=stage['end']<=1000 or stage['start']<=end:raise ValueError('阶段须为1—1000，起止顺序正确且不能重叠')
        if stage['mode'] not in ('additive','multiply') or not 0<=stage['value']<=(2000000 if stage['mode']=='additive' else 400):raise ValueError('阶段加额金额或比例无效，比例支持0—400%')
        end=stage['end']


def loss_amount(settings,stake,count):
    stages=settings.get('loss_stages')
    if stages is None:
        return stake+settings['loss_increment_cents'] if settings['stake_mode']=='additive' else ((stake*settings['multiplier_hundredths']+9999)//10000)*100
    stage=next((s for s in stages if s['start']<=count<=s['end']),None)
    if not stage:return stake
    if stage['mode']=='additive':return stake+stage['value']
    if not stage['value']:return stake
    return ((stake*(100+stage['value'])+9999)//10000)*100


def trend_allows(history,settings,direction):
    if not settings.get('trend_filter',False):return True,''
    count=settings.get('trend_lookback',20);rows=history[-count:]
    if len(rows)<count or any(b['id']!=a['id']+1 for a,b in zip(rows,rows[1:])) or any(r.get('verification') not in ('full_keno','crosschecked_abc') for r in rows):
        return False,'趋势过滤观察窗口不足、缺期或未核验'
    threshold=settings.get('trend_threshold_percent',70)
    size=sum(r['size']!=direction[0] for r in rows)
    parity=sum(r['parity']!=direction[1] for r in rows)
    if size*100>=count*threshold and parity*100>=count*threshold:
        return False,f'强趋势过滤：反向大小{size}/{count}，反向单双{parity}/{count}'
    return True,''


def enter(history,settings,state,direction,reason):
    allowed,why=trend_allows(history,settings,direction)
    state.pop('combo_pending',None)
    if not allowed:
        state['combo_observe_after']=history[-1]['id']
        return None,why+'，取消本次信号并重新观察'
    state.update(combo_active=True,combo_direction=direction,attempt=0,losses=0,combo_loss_count=0,
                 next_stake=settings['base_cents'],cycle=state.get('cycle',0)+1)
    return direction,reason


def signal(history,settings,state):
    if state.get('combo_active'):
        return state['combo_direction'],'本轮锁定'+state['combo_direction']
    pending=state.get('combo_pending')
    if pending:
        latest=history[-1]
        if latest['id']==pending['last_draw']:return None,'等待反转确认'
        if latest['id']!=pending['last_draw']+1 or latest.get('verification') not in ('full_keno','crosschecked_abc'):
            state.pop('combo_pending',None);state['combo_observe_after']=latest['id']
            return None,'反转确认开奖缺期或未核验，取消本次信号'
        current=latest['size']+latest['parity'];source=pending['source']
        changed=sum(a!=b for a,b in zip(current,source))
        if changed==2:
            state.pop('combo_pending',None);state['combo_observe_after']=latest['id']
            return None,'目标组合已开出，取消本次反转信号'
        if changed==1:return enter(history,settings,state,OPPOSITE[source],'单项反转已确认，下一期反买'+OPPOSITE[source])
        pending['last_draw']=latest['id']
        return None,'连续组合仍在延续，等待单项反转确认'
    count=settings['run_length']
    # A completed round consumes all observations through its closing draw.
    rows=[r for r in history if r['id']>state.get('combo_observe_after',0)][-count:]
    if len(rows)<count or any(b['id']!=a['id']+1 for a,b in zip(rows,rows[1:])):
        return None,'等待连续组合完整观察窗口'
    if any(r.get('verification') not in ('full_keno','crosschecked_abc') for r in rows):
        return None,'连续组合开奖尚未核验'
    combinations=[r['size']+r['parity'] for r in rows]
    if len(set(combinations))!=1 or combinations[0] not in OPPOSITE:
        return None,'尚未达到组合连续次数'
    direction=OPPOSITE[combinations[0]]
    if combinations[0] not in settings.get('entry_combos',list(OPPOSITE)):
        return None,'当前连续组合未在所选入场组合中'
    allowed,why=prior_opposite_allows(history,settings,combinations[0])
    if not allowed:return None,why
    if settings.get('confirmation_filter',False):
        state['combo_pending']={'source':combinations[0],'last_draw':rows[-1]['id']}
        return None,'组合连续次数达标，等待单项反转确认'
    return enter(history,settings,state,direction,f"{combinations[0]}连续{count}期，反买{direction}")


def settled(state,settings,outcome,stake,step,draw):
    if outcome=='win' or step>=settings['follow_draws']:
        state.update(combo_active=False,combo_observe_after=draw,attempt=0,losses=0,combo_loss_count=0,
                     next_stake=settings['base_cents'])
    elif outcome=='loss':
        count=state.get('combo_loss_count',0)+1
        state.update(combo_loss_count=count,next_stake=loss_amount(settings,stake,count))
    else:
        state['next_stake']=stake
