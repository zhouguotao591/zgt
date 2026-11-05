import json, os
from pathlib import Path
from .model import SCREENSHOT_ODDS
DEFAULT={
 'trend_simulation':{'enabled_axes':[],'initial_balance_per_strategy':100000},
 'host':'127.0.0.1','port':8028,'database':'data/pc28.sqlite3',
 'collector':{'maintenance_guard_enabled':True,'enabled':True,'allow_crosschecked_results':True,'interval_seconds':30,'timeout_seconds':15,'work_fallback_enabled':True,'work_fallback_url':'https://www.pc28.work/bff/market/latest','work_fallback_proxy':'','fallback_enabled':True,'fallback_url':'https://pc28.world/api/v1/draws/latest','fallback_timezone':'Asia/Shanghai','fallback_proxy':'','latest_url':'https://www.playnow.com/services2/keno/draw/latest/100','archive_url':'https://www.playnow.com/resources/documents/downloadable-numbers/KenoCurrentYear.zip','archive_interval_hours':24,'proxy':'','source_timezone':None,'number_order':'require_ascending','max_response_mb':64,'max_age_seconds':600,'field_map':{'records':'','id':'drawNumber','time':'drawDate','numbers':'numbers'}},
 'simulation':{'win_limit':0,'pause_draws':20,'round_restart':'condition_reset','enabled':False,'mode':'fixed','initial_balance':100000,'daily_loss_limit':3000,'max_drawdown':15000,'max_stake':400,'switch_cooldown_draws':3,'max_received_delay_seconds':60,'max_decision_age_seconds':120,'fixed':{'direction':'单','threshold':6,'max_bets':8,'lookback':30,'base_cents':10000,'multiplier_hundredths':200,'max_losses':3,'kind':'imbalance','run_length':5}},
 'odds':SCREENSHOT_ODDS.copy(),
 'research':{'train_months':3,'validation_months':1,'thresholds':[2,4,6,8,10,12,14,16],'max_bets':[3,5,8,13,21],'lookback':30,'include_reversal':True,'run_lengths':[3,5,8],'min_train_bets':1000,'min_validation_bets':300,'max_validation_drawdown':15000,'minimum_validation_roi':0.0,'top_train_candidates_per_direction':3,'require_flat_profit':True},
 'monthly_auto_select':True
}

def load(root):
    p=Path(root)/'config.json'
    if not p.exists():p.write_text(json.dumps(DEFAULT,ensure_ascii=False,indent=2),encoding='utf8')
    cfg=json.loads(p.read_text(encoding='utf-8-sig'));cfg.setdefault('trend_simulation',{'enabled_axes':[],'initial_balance_per_strategy':cfg['simulation']['initial_balance']});validate(cfg);return cfg

def validate(c):
    from .model import Strategy
    from .win_pause import validate as validate_pause
    validate_pause(c['simulation'].get('win_limit',0),c['simulation'].get('pause_draws',20))
    trend=c.get('trend_simulation',{})
    if not isinstance(trend.get('enabled_axes',[]),list) or len(set(trend.get('enabled_axes',[])))!=len(trend.get('enabled_axes',[])) or any(a not in ('parity','size') for a in trend.get('enabled_axes',[])):raise ValueError('走势策略只能选择单双和大小')
    if float(trend.get('initial_balance_per_strategy',100000))<=0:raise ValueError('走势策略初始本金必须大于0')
    from .trend_strategy import validate_settings as validate_trend
    rules=trend.get('strategies',{})
    if not isinstance(rules,dict) or any(k not in ('parity','size') for k in rules):raise ValueError('走势策略配置无效')
    for value in rules.values():validate_trend(value,c['odds'])
    if c['host']!='127.0.0.1':raise ValueError('仅允许绑定127.0.0.1')
    if not 1024<=int(c['port'])<=65535:raise ValueError('端口不合法')
    if c['simulation'].get('round_restart','immediate') not in ('condition_reset','immediate'):raise ValueError('轮结束恢复方式无效')
    if c['simulation']['mode'] not in ('fixed','monthly','adaptive'):raise ValueError('模拟模式错误')
    if not 15<=int(c['collector']['interval_seconds'])<=86400:raise ValueError('采集间隔至少15秒')
    if not 1<=int(c['collector']['timeout_seconds'])<=60:raise ValueError('网络超时范围1—60秒')
    if not 1<=int(c['collector']['max_response_mb'])<=128:raise ValueError('响应大小上限1—128MB')
    if c['collector']['number_order'] not in ('require_ascending','preserve','ascending'):raise ValueError('号码顺序配置错误')
    for k in ('initial_balance','daily_loss_limit','max_drawdown','max_stake'):
        if float(c['simulation'][k])<=0:raise ValueError('资金限制必须大于0')
    odds=c['odds']
    for key in ('normal','special_size','special_parity','high_stake_factor'):
        if key in odds and (type(odds[key]) is not int or not 100<=odds[key]<=1000):raise ValueError('赔率按百分之一倍填写')
    for key in ('high_stake_threshold_cents','min_stake_cents','max_stake_cents','max_return_per_draw_cents'):
        if key in odds and (type(odds[key]) is not int or odds[key]<=0):raise ValueError('玩法限额必须为正整数分')
    if odds.get('min_stake_cents',1)>odds.get('max_stake_cents',10**18):raise ValueError('玩法最小金额不能高于最大金额')
    if any(type(v) is not int or not 0<=v<=27 for v in odds.get('refund_winning_totals',[])+odds.get('full_payout_totals',[])):raise ValueError('退本金和值配置错误')
    r=c['research']
    if not 1<=r['train_months']<=24 or not 1<=r['validation_months']<=6:raise ValueError('训练/验证月数错误')
    if not r['thresholds'] or not r['max_bets'] or len(r['thresholds'])*len(r['max_bets'])>1000:raise ValueError('参数网格过大或为空')
    if c['collector']['source_timezone']:
        from zoneinfo import ZoneInfo
        try: ZoneInfo(c['collector']['source_timezone'])
        except Exception as e: raise ValueError('源时区不可用；Windows可能需要安装tzdata，或使用已知UTC偏移的ISO时间') from e
    if c['collector'].get('fallback_enabled',False):
        from zoneinfo import ZoneInfo
        try:ZoneInfo(c['collector'].get('fallback_timezone','Asia/Shanghai'))
        except Exception as e:raise ValueError('备用源时区无效') from e
    from urllib.parse import urlparse
    for key in ('latest_url','archive_url'):
        if urlparse(c['collector'][key]).scheme!='https':raise ValueError('采集地址必须是HTTPS')
    if c['collector'].get('fallback_enabled',False) and urlparse(c['collector'].get('fallback_url','')).scheme!='https':raise ValueError('备用源必须为HTTPS地址')
    if c['collector'].get('work_fallback_enabled',False) and urlparse(c['collector'].get('work_fallback_url','')).scheme!='https':raise ValueError('pc28.work备用源必须为HTTPS地址')
    if r['lookback']<2 or r['lookback']>1000:raise ValueError('研究窗口错误')
    if r['min_train_bets']<1 or r['min_validation_bets']<1:raise ValueError('最小样本数必须大于0')
    if not 1<=r['top_train_candidates_per_direction']<=20:raise ValueError('训练候选数范围1—20')
    if c['simulation']['max_received_delay_seconds']<1 or c['collector']['max_age_seconds']<1:raise ValueError('时效限制必须大于0')
    dbpath=Path(c['database'])
    if dbpath.is_absolute() or '..' in dbpath.parts:raise ValueError('数据库路径必须位于项目目录')
    Strategy(**c['simulation']['fixed'])
    from .model import stake_limit_reason
    if stake_limit_reason(c['simulation']['fixed']['base_cents'],odds):raise ValueError('基础下单金额超出玩法限额')
    for d in r['thresholds']:
        for k in r['max_bets']:Strategy(direction='大',threshold=d,max_bets=k,lookback=r['lookback'])
    return c

def save(root,c):
    validate(c);p=Path(root)/'config.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(c,ensure_ascii=False,indent=2),encoding='utf8');os.replace(tmp,p)
