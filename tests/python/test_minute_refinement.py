from dataclasses import replace
from datetime import datetime, timedelta
from hashlib import sha256
import json

import pytest
import qte
from qte.adapters.schwab_research import MINUTE_NS, NY, read_full_minute_sessions
from qte.minute_refinement import search
from qte.minute_replay import dataset, replay
from qte.strategies.minute_signals import Minute, Policy, features


def bars(day, count=390):
    stamp = int(datetime.fromisoformat(day+'T09:30').replace(tzinfo=NY).timestamp())*10**9
    return [qte.Bar('SPY',stamp+i*MINUTE_NS,stamp+(i+1)*MINUTE_NS,100+i*.01,101+i*.01,
                    99+i*.01,100+i*.01,200) for i in range(count)]


def test_future_minute_changes_leave_earlier_features_unchanged():
    dates = [(datetime(2026,10,12)+timedelta(days=i)).date().isoformat() for i in range(6)]
    sessions = {day:bars(day) for day in dates}
    day = dates[-1]
    first = features(sessions)[day]
    last = sessions[day][-1]
    sessions[day][-1] = qte.Bar('SPY',last.start_ns,last.end_ns,150,151,149,150,200)
    changed = features(sessions)[day]
    assert first[:-1] == changed[:-1]
    assert first[-1] != changed[-1]


def test_native_round_trips_honor_latency_hold_and_sides():
    day = '2026-10-12'
    session = {day: bars(day, 45)}
    b = session[day]
    first = Minute(21,100.3,100.2,2,1,1,2,1,.5,.4,0,False,.25)
    second = replace(first,index=31,breakout=-1,flow_direction=-1)
    minutes = {day:(first,second)}
    policy = Policy('breakout',1.5,False)
    result, skips = replay(dataset('SPY',session),session,minutes,policy,'stress_delayed')
    assert not skips and len(result)==2
    assert [r['sign'] for r in result] == [1,-1]
    assert [r['entry_ns'] for r in result] == [b[23].start_ns,b[33].start_ns]
    assert all(r['exit_ns']-r['entry_ns']==5*MINUTE_NS for r in result)
    assert all(r['execution_drag']>0 for r in result)


def test_threshold_loop_compares_both_sides_on_nonmonotonic_scores():
    called = []

    def score(x):
        called.append(x)
        pnl = 50-100*(x-2.5)**2
        metric = dict(net_pnl=pnl,trades=35,active_dates=9,net_without_best_date=pnl-5)
        return {cost:dict(combined=metric,symbols={'SPY':{'metrics':metric},'QQQ':{'metrics':metric}})
                for cost in ('base','stress','stress_delayed')}

    selected, rounds = search(1,2,3,score)
    assert selected == 2.5
    assert len(rounds)==3 and len(called)==len(set(called))
    assert rounds[0]['proposals'][0]['threshold']==1.5
    assert rounds[0]['proposals'][1]['threshold']==2.5
    assert all((r['after'][1]-r['after'][0])*2 == pytest.approx(r['before'][1]-r['before'][0]) for r in rounds)


def test_capture_refuses_missing_minute_even_with_valid_checksum(tmp_path):
    day = '2026-10-12'
    rows = bars(day)
    candles = [dict(datetime=b.start_ns//1_000_000,open=b.open,high=b.high,low=b.low,close=b.close,volume=b.volume) for b in rows]
    response = dict(symbol='SPY',empty=False,candles=candles)
    path = tmp_path/'schwab.json'

    def capture():
        fingerprint = sha256(json.dumps(response,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        path.write_text(json.dumps(dict(provider='schwab',response=response,response_canonical_json_sha256=fingerprint,
            request=dict(operation_id='priceHistory',query_parameters=dict(symbol='SPY',frequencyType='minute',frequency=1,
                startDate=rows[0].start_ns//1_000_000,endDate=rows[-1].end_ns//1_000_000,needExtendedHoursData=False)))))

    capture()
    assert len(read_full_minute_sessions(path,'SPY')[0][day])==390
    candles.pop(50)
    capture()
    with pytest.raises(ValueError,match='390-minute'):
        read_full_minute_sessions(path,'SPY')


def test_search_keeps_qualifying_neighborhood_over_high_pnl_sparse_candidate():
    def score(threshold):
        sparse = threshold < 2
        pnl = 200 if sparse else 40-10*abs(2.5-threshold)
        metric = dict(net_pnl=pnl, trades=15 if sparse else 40, active_dates=9,
                      net_without_best_date=pnl-5)
        return {cost: dict(combined=metric, symbols={'SPY':{'metrics':metric},'QQQ':{'metrics':metric}})
                for cost in ('base','stress','stress_delayed')}

    selected, rounds = search(1,2,3,score)
    assert selected == 2.5
    assert rounds[0]['round_winner'] == 2.5
    assert rounds[0]['after'][0] >= 2
