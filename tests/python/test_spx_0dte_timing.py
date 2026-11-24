from dataclasses import replace
import math

import pytest

from qte.spx_0dte_timing import Candle, mark, simulate


def test_clock_and_iv_produce_known_at_the_money_mark():
    spot, iv, t, close = 7000., .2, 950, 960
    expected = spot*math.erf(iv*math.sqrt((close-t)/(252*390))/math.sqrt(8))
    assert mark(spot,spot,iv,t,close,'call') == pytest.approx(expected,rel=1e-11)
    assert mark(spot,spot,iv,955,close,'call') < expected
    with pytest.raises(ValueError,match='cutoff'):
        mark(spot,spot,iv,960,close,'call')


def test_late_trade_requires_full_hold_and_uses_known_strike():
    bars={i:Candle(7000,7001,6999,7000) for i in range(570,960,5)}
    prior=dict(vix=22,regime='range',anchor=7000,expected_move=60)
    bars[950]=replace(bars[950],open=7060,high=7060)
    assert simulate(bars,5,960,prior,'call',950,.50,5,'delayed')[0]=='no_full_hold_before_cutoff'
    status,trade=simulate(bars,5,960,prior,'call',950,.50,5,'base')
    assert status=='filled' and trade['strike']==7015
    assert trade['exit_minute']==955
    assert trade['pnl']<0 and trade['exit_reason']=='stop_trigger'


def test_missing_exit_reserves_debit_after_entry():
    bars={i:Candle(7000,7001,6999,7000) for i in range(570,960,5)}
    del bars[605]
    prior=dict(vix=25,regime='range',anchor=7000,expected_move=60)
    status,trade=simulate(bars,5,960,prior,'call',600,.30,5,'base')
    assert status=='filled' and trade['exit_reason']=='unresolved'
    assert trade['pnl']==-trade['debit_dollars']
