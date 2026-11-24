from __future__ import annotations
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib, json

CONVERSION_VERSION = 'abc-columns-v1'
COMBO_ODDS = {'大单':4315,'小单':4750,'大双':4750,'小双':4315}
BALL_DIRECTIONS={ball+side:ball for ball in 'abc' for side in ('大','小')}
DIRECTION_AXES = {**dict.fromkeys(('大','小'),'size'),**dict.fromkeys(('单','双'),'parity'),**dict.fromkeys(COMBO_ODDS,'combo')}

ORDER_DIRECTION_AXES = {**DIRECTION_AXES,**BALL_DIRECTIONS}

def previous_direction(row,axis):
    if axis in ('a','b','c'):
        value=row[axis]
        if type(value) is not int or not 0<=value<=9:raise ValueError('球号须为0—9')
        return axis+('大' if value>=5 else '小')
    return row['size']+row['parity'] if axis=='combo' else row[axis]

def direction_hits(direction,size,parity,row=None):
    if direction in BALL_DIRECTIONS:
        if row is None:raise ValueError('三球大小结算需要对应球号')
        return direction==previous_direction(row,BALL_DIRECTIONS[direction])
    return direction in (size,parity,size+parity)


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def money(value):
    return int((Decimal(str(value))*100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))

def rounded_ratio(cents, hundredths):
    return (cents*hundredths+50)//100

def convert(numbers):
    if len(numbers)!=20 or any(type(n) is not int or not 1<=n<=80 for n in numbers) or len(set(numbers))!=20:
        raise ValueError('每期必须有20个不重复的1—80整数')
    groups=[sum(numbers[1+j:19:3]) for j in range(3)]
    digits=[n%10 for n in groups]; total=sum(digits)
    return dict(a=digits[0],b=digits[1],c=digits[2],total=total,size='大' if total>=14 else '小',parity='单' if total%2 else '双',group_sums=groups,conversion_version=CONVERSION_VERSION)

def parse_time(text):
    text=str(text).strip()
    try:
        return datetime.fromisoformat(text.replace('Z','+00:00'))
    except ValueError:
        for fmt in ('%Y-%m-%d %H:%M:%S','%m/%d/%Y %H:%M:%S','%m/%d/%Y %I:%M:%S %p','%Y-%m-%dT%H:%M:%S.%f'):
            try:return datetime.strptime(text,fmt)
            except ValueError:pass
    raise ValueError('不支持的开奖时间: '+text)

@dataclass(frozen=True)
class Strategy:
    direction: str
    threshold: int=8
    max_bets: int=3
    lookback: int=30
    base_cents: int=10000
    multiplier_hundredths: int=200
    max_losses: int=3
    kind: str='imbalance'
    run_length: int=0
    def __post_init__(self):
        for name in ('threshold','max_bets','lookback','base_cents','multiplier_hundredths','max_losses','run_length'):
            if type(getattr(self,name)) is not int:raise ValueError('策略参数必须是整数: '+name)
        if self.direction not in ('大','小','单','双','大小','单双'):raise ValueError('方向错误')
        if not 2<=self.lookback<=1000 or not 1<=self.max_bets<=500 or not 0<=self.max_losses<=10:raise ValueError('窗口或轮次上限错误')
        if not 1<=self.threshold<=self.lookback or self.base_cents<=0 or not 100<=self.multiplier_hundredths<=500:raise ValueError('差值/金额/倍数错误')
        if self.kind not in ('imbalance','reversal') or not 0<=self.run_length<=min(50,self.lookback):raise ValueError('策略类型错误')
    @property
    def key(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
    @property
    def axis(self):return 'size' if self.direction in ('大','小','大小') else 'parity'
    def resolve(self, history):
        if self.direction not in ('大小','单双'):return self
        if len(history)<self.lookback:return None
        values=[r[self.axis] for r in history[-self.lookback:]]
        labels=('大','小') if self.axis=='size' else ('单','双')
        counts={v:values.count(v) for v in labels}
        if abs(counts[labels[0]]-counts[labels[1]])<self.threshold:return None
        direction=min(labels,key=counts.get)
        if self.run_length and not all(v!=direction for v in values[-self.run_length:]):return None
        return replace(self,direction=direction)
    def triggered(self, history):
        if self.direction in ('大小','单双'):return self.resolve(history) is not None
        if len(history)<self.lookback:return False
        vals=[r[self.axis] for r in history[-self.lookback:]]
        own=vals.count(self.direction)
        return self.lookback-2*own>=self.threshold and (not self.run_length or all(v!=self.direction for v in vals[-self.run_length:]))

SCREENSHOT_ODDS = {
    'version':'screenshot-2026-11-01-v1',
    'normal':200,'special_size':200,'special_parity':200,
    'high_stake_threshold_cents':300100,'high_stake_factor':198,
    'refund_winning_totals':[0,27],'full_payout_totals':[13,14],
    'min_stake_cents':100,'max_stake_cents':2000000,
    'max_return_per_draw_cents':10000000
}


def stake_limit_reason(stake, odds):
    if stake < odds.get('min_stake_cents',1):return 'market_min_stake'
    if stake > odds.get('max_stake_cents',10**18):return 'market_max_stake'
    return ''


def settlement(stake, direction, total, hit, odds):
    # Old order snapshots retain their legacy 13/14 rules.
    if not hit:
        return {'pnl':-stake,'credit':0,'outcome':'loss','factor':0,'rule':'未命中'}
    if direction in BALL_DIRECTIONS:
        credit=min(rounded_ratio(stake,199),odds.get('max_return_per_draw_cents',10**18))
        return {'pnl':credit-stake,'credit':credit,'outcome':'win','factor':199,'rule':'单球大小命中按1.99倍，不适用和值退本规则'}
    if total in odds.get('refund_winning_totals',[]):
        return {'pnl':0,'credit':stake,'outcome':'refund','factor':100,'rule':'0/27命中退本金'}
    if direction in COMBO_ODDS:
        if (direction,total) in (('小单',13),('大双',14)):
            return {'pnl':0,'credit':stake,'outcome':'refund','factor':100,'rule':'小单13 / 大双14退本金'}
        thousandths=odds.get('combo_thousandths',COMBO_ODDS)[direction]
        credit=min((stake*thousandths+500)//1000,odds.get('max_return_per_draw_cents',10**18))
        return {'pnl':credit-stake,'credit':credit,'outcome':'win','factor':thousandths/10,'rule':'组合命中赔率'}
    if total in odds.get('full_payout_totals',[]):
        factor=odds['normal'];rule='13/14命中按2.0倍'
    elif stake >= odds.get('high_stake_threshold_cents',10**18):
        factor=odds['high_stake_factor'];rule='单注达到3001U，1.98倍'
    else:
        factor=odds['special_size' if direction in ('大','小') else 'special_parity'] if total in (13,14) else odds['normal']
        rule='普通命中赔率'
    credit=rounded_ratio(stake,factor)
    credit=min(credit,odds.get('max_return_per_draw_cents',10**18))
    return {'pnl':credit-stake,'credit':credit,'outcome':'win','factor':factor,'rule':rule}


def payout(stake, direction, total, hit, odds):
    return settlement(stake,direction,total,hit,odds)['pnl']


def wilson_interval(wins, count, z=1.96):
    if not count:
        return [None, None]
    p = wins/count
    denominator = 1+z*z/count
    center = (p+z*z/(2*count))/denominator
    half = z*((p*(1-p)/count+z*z/(4*count*count))**0.5)/denominator
    return [center-half, center+half]


def strict_int(value):
    if isinstance(value, bool):
        raise ValueError('布尔值不是合法号码/期号')
    result = int(value)
    if Decimal(str(value)) != result:
        raise ValueError('号码/期号不能包含小数')
    return result
