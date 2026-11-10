"""Hysteresis bands for additive win/loss sizing. All amounts are integer cents."""
FIELDS=tuple(side+'_'+name+'_cents' for side in ('low','high') for name in ('enter','exit','loss_increment','win_decrement'))
ZERO={key:0 for key in FIELDS}

def validate(settings):
 for key in FIELDS:
  if type(settings[key]) is not int or not 0<=settings[key]<=10000000000:raise ValueError('区间金额须为非负整数分，最高100000000U')
 for side in ('low','high'):
  if not any(settings[side+'_'+k+'_cents'] for k in ('enter','exit','loss_increment','win_decrement')):continue
  enter,exit=settings[side+'_enter_cents'],settings[side+'_exit_cents']
  if not enter or not exit or (enter>=exit if side=='low' else enter<=exit):raise ValueError('低区间退出金额须高于进入金额；高区间退出金额须低于进入金额，阈值不能为0')
 if settings['low_enter_cents'] and settings['high_enter_cents'] and settings['low_exit_cents']>settings['high_exit_cents']:raise ValueError('低区间退出金额不能高于高区间退出金额')

def adjustments(stake,settings,state):
 low=settings.get('low_enter_cents',0)>0;high=settings.get('high_enter_cents',0)>0
 band=state.get('amount_band','normal')
 if band=='low' and (not low or stake>settings.get('low_exit_cents',0)):band='normal'
 if band=='high' and (not high or stake<settings.get('high_exit_cents',0)):band='normal'
 if band not in ('low','high'):band='normal'
 if band=='normal':
  if low and stake<settings['low_enter_cents']:band='low'
  elif high and stake>=settings['high_enter_cents']:band='high'
 if band=='normal':state.pop('amount_band',None)
 else:state['amount_band']=band
 return (settings.get(band+'_loss_increment_cents',0),settings.get(band+'_win_decrement_cents',0)) if band!='normal' else (settings['loss_increment_cents'],settings['win_decrement_cents'])

def description(s):
 result=''
 for side,label,enter,exit in [('low','低区间','低于','高于'),('high','高区间','达到或高于','低于')]:
  if s.get(side+'_enter_cents',0):result+=f"；{label}：金额{enter}{s[side+'_enter_cents']/100:g}U进入，亏加{s[side+'_loss_increment_cents']/100:g}U、赢减{s[side+'_win_decrement_cents']/100:g}U，{exit}{s[side+'_exit_cents']/100:g}U退出"
 return result
