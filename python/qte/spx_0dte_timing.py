"""Causal sampled-path pricing scenarios for SPXW. Prices are model marks."""
from dataclasses import dataclass
from datetime import datetime
import math
from zoneinfo import ZoneInfo

from .gamma_surface import _black76

NY = ZoneInfo('America/New_York')
SLOTS = (*range(570, 931, 30), 950)
FAMILIES = ('call', 'low_vol_call', 'confirmed_call', 'confirmed_put')
TARGETS = (.30,.50)
HOLDS = (5,10)
SCENARIOS = {
    'base': (0,.10,1.25,1.,0.),
    'wide': (0,.25,2.50,1.,0.),
    'iv_crush': (0,.25,2.50,1.,-.02),
    'higher_entry_iv': (0,.25,2.50,1.25,0.),
    'delayed': (1,.25,2.50,1.,0.),
}
STOP = .35


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float


def decode_capture(doc, step):
    if doc['request']['interval'] != {1:'minute',5:'5minute'}[step]:
        raise ValueError('Incorrect capture cadence')
    out={}
    for result in doc['response'].get('data',{}).get('results') or ():
        if result is None or result['symbol']!='SPX':
            raise ValueError('Expected SPX index')
        for row in result['bars'] or ():
            if not row or row.get('interpolated'):
                continue
            dt=datetime.fromisoformat(row['begins_at'].replace('Z','+00:00')).astimezone(NY)
            m=dt.hour*60+dt.minute
            if m<570 or m+step>960 or (m-570)%step or dt.second or dt.microsecond:
                continue
            values=[float(row[key+'_value']) for key in ('open','high','low','close')]
            if not all(math.isfinite(v) for v in values) or not 0<values[2]<=min(values[0],values[3])<=max(values[0],values[3])<=values[1]:
                continue
            out.setdefault(dt.date().isoformat(),{})[m]=Candle(*values)
    return out


def right_at(family, bars, slot, step, prior):
    if family=='call':
        return 'call'
    if family=='unconditional_put':
        return 'put'  # Counterfactual control, outside the frozen candidate slate.
    if family=='low_vol_call':
        return 'call' if prior['vix']<15 else None
    before=bars.get(slot-step)
    recent=bars.get(slot-step-15)
    old=bars.get(slot-step-30)
    if not before or not recent or not old:
        return None
    if family=='confirmed_call':
        return 'call' if (before.close > recent.close > old.close and
            prior['regime'] not in {'stress_selloff','high_vol_down','trend_down'} and
            before.close < prior['anchor']+2*prior['expected_move']) else None
    if family=='confirmed_put':
        return 'put' if (before.close < recent.close < old.close and
             prior['regime'] in {'stress_selloff','high_vol_down','trend_down'} and
             (before.close > prior['anchor']-2*prior['expected_move'] or prior['regime']=='stress_selloff')) else None
    raise ValueError('Unknown timing condition')


def mark(spot,strike,iv,t,close,right):
    if t>=close:
        raise ValueError('Expiry cutoff: no modeled liquidation')
    return _black76(forward=spot, strike=strike, volatility=iv, time_years=(close-t)/(252*390), rate=0., option_type=right)['price']


def tick_price(mark_value, half_spread, buy):
    # Stress spreads and a conservative $0.10 quote grid in every premium tier.
    raw=mark_value+half_spread if buy else max(0.,mark_value-half_spread)
    return (math.ceil(raw*10-1e-10) if buy else math.floor(raw*10+1e-10))/10


def simulate(bars, step, close, prior, family, slot, target, hold, scenario):
    delay,half_spread,fee,iv_scale,exit_shift=SCENARIOS[scenario]
    if slot<570+step or slot>=close or slot-step not in bars:
        return 'no_prior_completed_bar',None
    entry=slot+delay*step
    deadline=entry+hold
    if deadline>=close:
        return 'no_full_hold_before_cutoff',None
    right=right_at(family,bars,slot,step,prior)
    if right is None:
        return 'no_signal',None
    seen=bars[slot-step].close
    strike=5*(math.ceil(seen*1.002/5) if right=='call' else math.floor(seen*.998/5))
    iv=prior['vix']/100*iv_scale
    if entry not in bars:
        return 'no_entry_observation',None
    mid=mark(bars[entry].open,strike,iv,entry,close,right)
    if mid<.20:
        return 'premium_below_model_floor',None
    ask=tick_price(mid,half_spread,True)
    debit=100*ask+fee
    out_iv=max(.01,iv+exit_shift)
    def out_price(spot,t):
        return max(0.,100*tick_price(mark(spot,strike,out_iv,t,close,right),half_spread,False)-fee)
    reason='time'
    proceeds=0.
    t=entry+step
    while t<=deadline:
        if t not in bars or t-step not in bars:
            reason='unresolved'
            break
        # Previous close is observed before the exit order at the next opening
        # boundary. The next open can gap through a target or stop.
        trigger=out_price(bars[t-step].close,t)/debit-1
        if trigger>=target or trigger<=-STOP or t==deadline:
            reason='target_trigger' if trigger>=target else 'stop_trigger' if trigger<=-STOP else 'time'
            proceeds=out_price(bars[t].open,t)
            break
        t+=step
    return 'filled',dict(slot=slot,entry_minute=entry,exit_minute=t,strike=strike,right=right,
        entry_spot=bars[entry].open,debit_dollars=debit,proceeds_dollars=proceeds,
        pnl=proceeds-debit,net_return=proceeds/debit-1,exit_reason=reason)
