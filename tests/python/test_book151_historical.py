"""Research harness safeguards, independent of optional local market captures."""
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('book151_historical', Path(__file__).resolve().parents[2]/'examples/book151_historical.py')
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def test_gate_uses_decision_date_prior_vintage_and_fails_closed():
    now = int(study.utc_time('2024-02-01T21:00:00Z').timestamp())*study.NS
    row = dict(asof='2024-01-31', regime='range')
    assert study.gate(now, now, now+1, 'regime_veto', {'2024-02-01': row})
    for invalid in ({}, {'2024-02-02': row}, {'2024-02-01': dict(asof='2024-02-01', regime='range')},
                    {'2024-02-01': dict(row, regime='stress_selloff')},
                    {'2024-02-01': dict(row, regime='invented')}):
        assert not study.gate(now, now, now+1, 'regime_veto', invalid)
    assert not study.gate(now, now-1, now, 'ungated', {})
    assert study.gate(now, now, now+1, 'ungated', {})


def test_capture_dates_are_utc_and_conflicting_duplicates_fail(tmp_path):
    row = dict(begins_at='2024-01-02T00:00:00Z', open_price='100', high_price='102',
               low_price='99', close_price='101', volume=1000)
    doc = dict(request=dict(start_time='2024-01-01', end_time='2024-01-03', interval='day', bounds='regular', adjustment_type='all'),
               response=dict(data=dict(results=[dict(symbol='SPY', bars=[row])])) )
    (tmp_path/'day-all-2024.json').write_text(json.dumps(doc))
    panel, sources, excluded = study.load_daily(tmp_path)
    assert panel['SPY']['2024-01-02']['close'] == 101
    assert len(sources) == 1 and not excluded
    row['close_price'] = '100'
    (tmp_path/'day-all-2024-duplicate.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='conflicting'):
        study.load_daily(tmp_path)


def test_regime_veto_exits_next_open_and_reconciles_terminal_cash():
    rows, regimes = [], {}
    for i in range(70):
        date = (datetime(2024, 1, 1)+timedelta(days=i)).date()
        price = 100+i
        rows.append(dict(date=date.isoformat(), open=price, high=price+1, low=price-1, close=price, volume=1000))
        regimes[date.isoformat()] = dict(asof=(date-timedelta(days=1)).isoformat(),
                                         regime='range' if i<30 else 'stress_selloff')
    bars = study.make_bars('TEST', rows)
    result = study.run(bars, bars[25].end_ns, bars[-2].end_ns, 'book_single_ma', 'regime_veto', study.COSTS['stress'], regimes)
    assert len(result['fills']) == 2
    assert result['fills'][0]['time_ns'] == bars[26].start_ns
    assert result['fills'][1]['time_ns'] == bars[31].start_ns
    assert result['fills'][1]['side'] == 'SELL'
    assert result['summary']['closed_trades'] == 1


def test_capture_adjustment_contract_is_not_inferred_from_filename(tmp_path):
    doc = dict(request=dict(interval='day', bounds='regular', adjustment_type='none'))
    (tmp_path/'day-all-2024.json').write_text(json.dumps(doc))
    with pytest.raises(ValueError, match='all-adjusted'):
        study.load_daily(tmp_path)
