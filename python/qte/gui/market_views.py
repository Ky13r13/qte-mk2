"""Small, verified market-chart projections (never a raw artifact browser)."""
from __future__ import annotations

import csv
import io
import math

from .artifacts import ArtifactError
from .lab_views import _csv, _invalid, _read, _validated, _selected_window, _decisions

MAX_CANDLES = 4096
MAX_RUN_ROWS = 16384


def _number(text: str) -> float:
    try: value = float(text)
    except (ValueError, OverflowError): raise _invalid()
    if not math.isfinite(value): raise _invalid()
    return value


def _integer(text: str) -> int:
    try: value = int(text)
    except ValueError: raise _invalid()
    if str(value) != text or not -(2 ** 63) <= value < 2 ** 63: raise _invalid()
    return value


def _lab_candles(artifact, model, window, symbol):
    rows = _csv(artifact, f"{model['timeframe']}/data/{window['name']}.csv",
                ["symbol", "start_ns", "end_ns", "open", "high", "low", "close", "volume"])
    symbols = sorted(set(window['symbols']))
    if symbol is None: symbol = symbols[0] if len(symbols) == 1 else None
    if symbol not in symbols:
        raise ArtifactError("symbol_not_found", 404, "Recorded symbol unavailable.")
    candles, previous_end, counts = [], {}, {item: 0 for item in symbols}
    for row in rows:
        if row['symbol'] not in symbols: raise _invalid()
        start, end = _integer(row['start_ns']), _integer(row['end_ns'])
        op, hi, lo, close, volume = (_number(row[key]) for key in ('open', 'high', 'low', 'close', 'volume'))
        if not (window['start_ns'] <= start < end <= window['end_ns'] and end - start == window['interval_ns'] and op > 0 and hi > 0 and lo > 0 and close > 0
                and volume >= 0 and lo <= min(op, close) <= max(op, close) <= hi): raise _invalid()
        if row['symbol'] in previous_end and start < previous_end[row['symbol']]: raise _invalid()
        previous_end[row['symbol']] = end; counts[row['symbol']] += 1
        if row['symbol'] == symbol:
            candles.append({'start_ns': start, 'end_ns': end, 'open': op, 'high': hi, 'low': lo,
                            'close': close, 'volume': volume})
    if len(rows) != window['bar_count'] or not all(counts.values()) or len(candles) > MAX_CANDLES:
        raise ArtifactError("market_range_too_large", 422, "Recorded candle range is unavailable; choose a narrower window.")
    return symbols, symbol, candles


def _run(artifact, model, window, candidate, cost, symbol):
    scenario = model['scenarios'].get((candidate, window['name'], cost))
    if scenario is None: return {'candidate': candidate, 'status': 'missing'}
    if scenario['status'] != 'ok': return {'candidate': candidate, 'status': 'error', 'error': scenario['error'] or 'Recorded run failed.'}
    base = f"{model['timeframe']}/runs/{window['name']}--{candidate}--{cost}"
    fill_name, equity_name = f'{base}/fills.csv', f'{base}/equity.csv'
    if fill_name not in artifact.known or equity_name not in artifact.known:
        return {'candidate': candidate, 'status': 'missing'}
    fills = _csv(artifact, fill_name, ['id', 'order_id', 'symbol', 'side', 'quantity', 'timestamp_ns', 'price', 'commission'])
    equity = _csv(artifact, equity_name, ['timestamp_ns', 'equity', 'gross_exposure'])
    if len(fills) != scenario['fill_count'] or len(fills) > MAX_RUN_ROWS or len(equity) > MAX_RUN_ROWS:
        raise ArtifactError('market_range_too_large', 422, 'Recorded run range is unavailable; choose a narrower window.')
    output_fills, previous, fill_ids = [], None, set()
    for row in fills:
        timestamp = _integer(row['timestamp_ns']); quantity = _integer(row['quantity'])
        fill_id, order_id = _integer(row['id']), _integer(row['order_id'])
        price, commission = _number(row['price']), _number(row['commission'])
        if (row['symbol'] not in window['symbols'] or row['side'] not in {'BUY', 'SELL'} or quantity <= 0 or price <= 0 or commission < 0
                or fill_id <= 0 or order_id <= 0 or fill_id in fill_ids
                or not window['start_ns'] <= timestamp <= window['end_ns'] or previous is not None and timestamp < previous): raise _invalid()
        fill_ids.add(fill_id)
        previous = timestamp
        if row['symbol'] == symbol:
            output_fills.append({'timestamp_ns': timestamp, 'order_id': row['order_id'], 'side': row['side'],
                                 'quantity': quantity, 'price': price, 'commission': commission, 'signal_ns': None})
    output_equity, previous = [], None
    for row in equity:
        timestamp = _integer(row['timestamp_ns']); value, exposure = _number(row['equity']), _number(row['gross_exposure'])
        if (not window['start_ns'] <= timestamp <= window['end_ns'] or exposure < 0 or previous is not None and timestamp < previous): raise _invalid()
        previous = timestamp; output_equity.append({'timestamp_ns': timestamp, 'equity': value, 'gross_exposure': exposure})
    if (not output_equity or output_equity[0]['timestamp_ns'] != window['start_ns']
            or output_equity[-1]['timestamp_ns'] != window['end_ns']
            or output_equity[0]['equity'] != model['context']['initial_cash']
            or output_equity[-1]['equity'] != scenario['final_equity']):
        raise _invalid()
    return {'candidate': candidate, 'status': 'ok', 'signal_status': 'not_recorded',
            'fills': output_fills, 'equity': output_equity}


def project_lab_market(artifact, timeframe=None, window=None, candidate=None, compare=None, cost='base', symbol=None,
                       authorize=None):
    model = _validated(artifact, timeframe)
    selected = _selected_window(model, window)
    if cost not in {'base', 'stress'}: raise ArtifactError('invalid_query', 400, 'Invalid market cost scenario.')
    names = set(model['slate_names'])
    # This is display selection only: never rank or imply a recommendation.
    if candidate is None: candidate = model['slate_names'][0]
    if candidate not in names or compare is not None and compare not in names or compare == candidate:
        raise ArtifactError('invalid_query', 400, 'Invalid market candidate.')
    requested = [candidate] + ([] if compare is None else [compare])
    if authorize is not None:
        names = [f"{model['timeframe']}/data/{selected['name']}.csv"]
        for item in requested:
            scenario = model['scenarios'].get((item, selected['name'], cost))
            if scenario is not None and scenario['status'] == 'ok':
                base = f"{model['timeframe']}/runs/{selected['name']}--{item}--{cost}"
                names.extend((f'{base}/fills.csv', f'{base}/equity.csv'))
        authorize(names)
    symbols, selected_symbol, candles = _lab_candles(artifact, model, selected, symbol)
    decisions, _ = _decisions(artifact, model, selected)
    previous_decision = None
    for decision in decisions:
        timestamp = decision['timestamp_ns']
        if (previous_decision is not None and timestamp <= previous_decision
                or not selected['start_ns'] < timestamp <= selected['end_ns']
                or not isinstance(decision['macro_regime'], str) or not decision['macro_regime']
                or not isinstance(decision['volatility_regime'], str) or not decision['volatility_regime']
                or any(not isinstance(item, str) or not item for item in decision['permitted_families'])):
            raise _invalid()
        previous_decision = timestamp
    # A decision is available only at the recorded close timestamp.  Keep the
    # explicit alias for consumers that render an as-of band, never bar-open.
    decisions = [{**item, 'captured_at_ns': item['timestamp_ns']} for item in decisions]
    runs = [_run(artifact, model, selected, item, cost, selected_symbol) for item in requested]
    return {'status': 'recorded', 'symbols': symbols, 'symbol': selected_symbol,
            'window': {key: selected[key] for key in ('name', 'role', 'start_ns', 'end_ns')},
            'cost_scenario': cost, 'costs': model['context']['base_costs' if cost == 'base' else 'stressed_costs'],
            'regime_source': 'recorded as-of filter decisions on scripted synthetic inputs; not per-candidate signal or generator ground truth',
            'candlestick_policy': 'raw_no_aggregation', 'candles': candles, 'regimes': decisions, 'runs': runs,
            'candidates': [{'candidate': item, 'status': (model['scenarios'].get((item, selected['name'], cost)) or {'status': 'missing'})['status']}
                           for item in model['slate_names']],
            'warnings': ['Fill markers are recorded executions; signal timestamps were not recorded and are null.',
                         'Gaps remain raw; candles are not aggregated.']}
