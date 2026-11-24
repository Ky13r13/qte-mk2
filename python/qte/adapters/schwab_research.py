"""Validate Schwab daily and minute histories at the provider boundary."""
from datetime import datetime, time
from hashlib import sha256
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from qte import Bar
from qte.json_input import strict_json

NY = ZoneInfo('America/New_York')
MINUTE_NS = 60_000_000_000


def _history(path, symbol, frequency):
    raw = path.read_bytes()
    capture = strict_json(raw)
    response = capture['response']
    canonical = json.dumps(response, allow_nan=False, sort_keys=True, separators=(',', ':')).encode()
    if sha256(canonical).hexdigest() != capture['response_canonical_json_sha256']:
        raise ValueError('Schwab capture response checksum mismatch')
    request = capture['request']
    parameters = request['query_parameters']
    if (capture['provider'] != 'schwab' or request['operation_id'] != 'priceHistory'
            or parameters['symbol'] != symbol or response['symbol'] != symbol or response['empty'] is not False
            or parameters['frequencyType'] != frequency or parameters['frequency'] != 1
            or parameters['needExtendedHoursData'] is not False):
        raise ValueError(f'Expected a nonempty regular-session Schwab {frequency} history for the stated symbol')
    return response['candles'], parameters, sha256(raw).hexdigest()


def _ohlcv(row):
    prices = [row[name] for name in ('open', 'high', 'low', 'close')]
    volume = row['volume']
    if (any(type(v) not in (float, int) or not math.isfinite(v) for v in [*prices, volume])
            or volume < 0 or not 0 < prices[2] <= min(prices[0], prices[3])
            or max(prices[0], prices[3]) > prices[1]):
        raise ValueError('Invalid Schwab OHLCV')
    return prices, volume


def read_daily_closes(path: Path, symbol: str):
    """Daily labels and finalized high/close used only on later sessions."""
    candles, params, digest = _history(path, symbol, 'daily')
    days = {}
    previous = None
    for candle in candles:
        stamp = candle['datetime']
        if type(stamp) is not int or (previous is not None and stamp <= previous):
            raise ValueError('Unordered daily history')
        previous = stamp
        if not params['startDate'] <= stamp < params['endDate']:
            continue  # Schwab can include the previous close outside the request.
        day = datetime.fromtimestamp(stamp / 1000, NY).date().isoformat()
        if day in days:
            raise ValueError('Duplicate daily candle')
        prices, _ = _ohlcv(candle)
        days[day] = (prices[1], prices[3])
    if not days:
        raise ValueError('Empty daily history')
    return days, digest


def read_minute_sessions(path: Path, symbol: str):
    candles, parameters, digest = _history(path, symbol, 'minute')
    first, last = parameters['startDate'], parameters['endDate']
    sessions = {}
    previous = None
    for row in candles:
        stamp = row['datetime']
        if type(stamp) is not int or stamp % 60_000 or not first <= stamp < last or (previous is not None and stamp <= previous):
            raise ValueError('Off-grid, out-of-window or unordered Schwab minute')
        previous = stamp
        local = datetime.fromtimestamp(stamp / 1000, NY)
        if not time(9, 30) <= local.time() < time(16):
            raise ValueError('Unexpected minute outside the requested cash session')
        prices, volume = _ohlcv(row)
        start_ns = stamp * 1_000_000
        bar = Bar(symbol, start_ns, start_ns + MINUTE_NS, *prices, volume)
        sessions.setdefault(local.date().isoformat(), []).append(bar)
    if not sessions:
        raise ValueError('No complete sessions in the capture')
    return sessions, digest


def read_full_minute_sessions(path: Path, symbol: str):
    sessions, digest = read_minute_sessions(path, symbol)
    for day, bars in sessions.items():
        if len(bars) != 390 or any(a.end_ns != b.start_ns for a, b in zip(bars, bars[1:])):
            raise ValueError(f'{symbol} {day} is not a complete 390-minute regular session')
    return sessions, digest
