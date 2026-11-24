"""Forward market-hours journals and their conversion to options-master sessions.

This is the adapter boundary for the forward collector. Raw Schwab responses
stay in the journal; this module turns them into the canonical replay session
used by ``weekly_reversal`` and ``options_master``.

Timestamp policy
----------------
* Every journal line carries the collector's receipt clock ``received_utc_ns``.
  A value becomes *available* to a strategy at its receipt time, never earlier.
* Provider quote times (``quoteTime``/``quoteTimeInLong``, ms) are *event* times.
  A quote is admitted only if it is real time, ``Normal`` status, two-sided,
  sized and no older than ``max_quote_age_ms`` at receipt.
* Underlying bars are point samples on a fixed grid: the close at grid time
  ``t`` is the NBBO mid of the newest admitted underlying quote whose event
  time is at or before ``t`` and no older than the age limit. The bar becomes
  available at ``max(t, receipt)``. Missing samples leave a gap; the replay
  resets its excursion state across gaps.
* Option quotes keep their provider event time and collector receipt time.
  Repeated polls of the same quote keep the earliest receipt.
* No news feed is collected. ``news_flags`` is always empty, so the regime
  gate can only veto observable excursion shocks.

Contract selection is a pure function of journaled opening chain responses.
The collector calls it live to decide what to poll and records the result;
the builder recomputes it from the same responses and rejects a mismatch.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date, datetime, time as dtime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .json_input import strict_json

NY = ZoneInfo('America/New_York')
SCHEMA = 'qte-forward-journal-v1'
SECOND_NS = 1_000_000_000
DAY_NS = 86_400 * SECOND_NS
MAX_LINE = 8 * 1024 * 1024
OPERATIONS = ('optionChain', 'quotes')
EVENTS = ('start', 'resume', 'response', 'error', 'selection', 'stop')
STOP_REASONS = ('session_close', 'interrupted', 'no_opening_selection', 'fatal_error')
SYMBOL = re.compile(r'[A-Z][A-Z0-9.]{0,5}')
OPTION_KEY = re.compile(r'([A-Z][A-Z0-9.]{0,5}) *([0-9]{6})([CP])([0-9]{8})')
HHMM = re.compile(r'([01][0-9]|2[0-3]):([0-5][0-9])')

DEFAULT_RULES = dict(
    underlying='SPY',
    open_et='09:30',
    close_et='16:00',
    allowed_close_et=['13:00', '16:00'],
    bar_interval_s=60,
    max_quote_age_ms=10_000,
    opening_deadline_s=300,
    target_opening_ask=4.0,
    weekly_min_days=5,
    weekly_max_days=9,
    today_strike_count=4,
    weekly_strike_count=60,
)


class JournalError(ValueError):
    """The journal is malformed or internally inconsistent."""


def encode(value) -> bytes:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value) -> str:
    return sha256(encode(value)).hexdigest()


def validate_rules(rules: dict) -> dict:
    if type(rules) is not dict or set(rules) != set(DEFAULT_RULES):
        raise ValueError('builder rules must contain exactly the documented keys')
    if type(rules['underlying']) is not str or not SYMBOL.fullmatch(rules['underlying']):
        raise ValueError('underlying must be an uppercase equity symbol')
    for key in ('open_et', 'close_et'):
        if type(rules[key]) is not str or not HHMM.fullmatch(rules[key]):
            raise ValueError(f'{key} must be HH:MM')
    if (type(rules['allowed_close_et']) is not list or not rules['allowed_close_et']
            or any(type(v) is not str or not HHMM.fullmatch(v) or v <= rules['open_et']
                   for v in rules['allowed_close_et'])
            or rules['close_et'] not in rules['allowed_close_et']):
        raise ValueError('allowed_close_et must list HH:MM closes after the open, including close_et')
    for key, low, high in (('bar_interval_s', 1, 300), ('max_quote_age_ms', 1, 30_000),
                           ('opening_deadline_s', 1, 300), ('weekly_min_days', 5, 9),
                           ('weekly_max_days', 5, 9), ('today_strike_count', 1, 50),
                           ('weekly_strike_count', 2, 200)):
        if type(rules[key]) is not int or not low <= rules[key] <= high:
            raise ValueError(f'{key} must be an integer in [{low}, {high}]')
    if rules['weekly_min_days'] > rules['weekly_max_days']:
        raise ValueError('weekly day bounds are inverted')
    if 300 % rules['bar_interval_s']:
        raise ValueError('bar_interval_s must divide the five-minute decision grid')
    target = rules['target_opening_ask']
    if type(target) not in (int, float) or not 0 < target < 6.5:
        raise ValueError('target_opening_ask must be positive and below the $650 debit cap')
    return rules


def session_bounds(session_date: date, open_et: str, close_et: str) -> tuple[int, int]:
    def at(text):
        hour, minute = map(int, text.split(':'))
        local = datetime.combine(session_date, dtime(hour, minute), NY)
        return int(local.astimezone(timezone.utc).timestamp()) * SECOND_NS
    open_ns, close_ns = at(open_et), at(close_et)
    if not open_ns < close_ns:
        raise ValueError('session close must follow open')
    return open_ns, close_ns


def et_date(timestamp_ns: int) -> date:
    return datetime.fromtimestamp(timestamp_ns / SECOND_NS, NY).date()


# --------------------------------------------------------------------------- journal

def read_journal(path: Path) -> dict:
    """Validate one journal. A single incomplete final line (crash mid-write) is dropped."""
    raw = Path(path).read_bytes()
    lines = raw.split(b'\n')
    truncated = b''
    if lines and lines[-1] == b'':
        lines.pop()
    elif lines:
        truncated = lines.pop()
    records, previous_received, start = [], 0, None
    for number, line in enumerate(lines):
        if len(line) > MAX_LINE:
            raise JournalError(f'line {number}: exceeds size limit')
        try:
            record = strict_json(line)
        except ValueError as error:
            raise JournalError(f'line {number}: invalid JSON') from error
        if type(record) is not dict or record.get('event') not in EVENTS:
            raise JournalError(f'line {number}: unknown event')
        if record.get('seq') != number:
            raise JournalError(f'line {number}: sequence gap or reordering')
        received = record.get('received_utc_ns')
        if type(received) is not int or received <= 0 or received < previous_received:
            raise JournalError(f'line {number}: receipt clock missing or moved backward')
        previous_received = received
        event = record['event']
        if number == 0:
            if event != 'start':
                raise JournalError('journal must begin with a start record')
            start = _check_start(record)
        elif event == 'start':
            raise JournalError(f'line {number}: duplicate start')
        elif event in ('response', 'error'):
            _check_exchange(record, number)
        elif event == 'stop' and record.get('reason') not in STOP_REASONS:
            raise JournalError(f'line {number}: unknown stop reason')
        records.append(record)
    if start is None:
        raise JournalError('empty journal')
    return dict(start=start, records=records, sha256=sha256(raw[:len(raw) - len(truncated)]).hexdigest(),
                truncated_tail_bytes=len(truncated),
                first_received_utc_ns=records[0]['received_utc_ns'],
                last_received_utc_ns=records[-1]['received_utc_ns'])


def _check_start(record):
    keys = {'event', 'seq', 'received_utc_ns', 'schema', 'session_date', 'underlying', 'open_et',
            'close_et', 'interval_ms', 'contract_sha256', 'rules', 'collector_sha256'}
    if set(record) != keys or record['schema'] != SCHEMA:
        raise JournalError('unsupported journal start record')
    try:
        day = date.fromisoformat(record['session_date'])
        rules = validate_rules(record['rules'])
    except (TypeError, ValueError) as error:
        raise JournalError(f'invalid start record: {error}') from None
    if record['underlying'] != rules['underlying'] or record['open_et'] != rules['open_et']:
        raise JournalError('start record disagrees with its rules')
    if record['close_et'] not in rules['allowed_close_et']:
        raise JournalError('declared close is not an allowed session close')
    if type(record['interval_ms']) is not int or not 1000 <= record['interval_ms'] <= 60_000:
        raise JournalError('poll interval must be 1-60 seconds')
    if et_date(record['received_utc_ns']) != day:
        raise JournalError('journal started on a different New York date than its session')
    return record


def _check_exchange(record, number):
    common = {'event', 'seq', 'received_utc_ns', 'request_started_utc_ns', 'operation', 'query'}
    keys = common | ({'response_sha256', 'response'} if record['event'] == 'response' else {'error'})
    if set(record) != keys or record['operation'] not in OPERATIONS or type(record['query']) is not dict:
        raise JournalError(f'line {number}: malformed {record["event"]} record')
    started = record['request_started_utc_ns']
    if type(started) is not int or not 0 < started <= record['received_utc_ns']:
        raise JournalError(f'line {number}: request start after receipt')
    if record['event'] == 'response' and digest(record['response']) != record['response_sha256']:
        raise JournalError(f'line {number}: response digest mismatch')


# ------------------------------------------------------------------ contract selection

def _ms_to_ns(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(f'{name} must be a positive integer millisecond timestamp')
    return value * 1_000_000


def _finite(value, name):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    return float(value)


def _chain_header(response, underlying):
    if type(response) is not dict or response.get('status') != 'SUCCESS':
        raise ValueError('chain status is not SUCCESS')
    if response.get('symbol') != underlying or response.get('isDelayed') is not False:
        raise ValueError('chain symbol mismatch or delayed chain')
    under = response.get('underlying')
    if type(under) is not dict or under.get('symbol') != underlying or under.get('delayed') is not False:
        raise ValueError('chain lacks a real-time underlying quote')
    bid, ask = _finite(under.get('bid'), 'underlying bid'), _finite(under.get('ask'), 'underlying ask')
    if not 0 < bid <= ask:
        raise ValueError('underlying quote is not two-sided')
    return (bid + ask) / 2, _ms_to_ns(under.get('quoteTime'), 'underlying quoteTime')


def _legs(response, side, underlying):
    """Yield (expiry date, contract) for standard 100-share physically settled contracts."""
    book = response.get('callExpDateMap' if side == 'C' else 'putExpDateMap')
    if type(book) is not dict:
        raise ValueError('chain lacks an expiry map')
    for key, strikes in book.items():
        if type(key) is not str or type(strikes) is not dict:
            raise ValueError('malformed expiry map')
        expiry = date.fromisoformat(key.split(':', 1)[0])
        for rows in strikes.values():
            if type(rows) is not list:
                raise ValueError('malformed strike list')
            for leg in rows:
                if type(leg) is not dict:
                    raise ValueError('malformed contract')
                match = OPTION_KEY.fullmatch(leg.get('symbol', '')) if type(leg.get('symbol')) is str else None
                if (not match or match[1] != underlying or match[3] != side
                        or leg.get('putCall') != ('CALL' if side == 'C' else 'PUT')):
                    continue
                if (leg.get('multiplier') != 100 or leg.get('nonStandard') is not False
                        or leg.get('mini') is not False or leg.get('settlementType') != 'P'
                        or leg.get('exerciseType') not in ('A', 'E')
                        or leg.get('optionDeliverablesList') != [dict(assetType='STOCK', currencyType=None,
                                                                      deliverableUnits=100.0, symbol=underlying)]):
                    continue
                yield expiry, leg


def _expiry_ns(leg):
    text = leg.get('expirationDate')
    if type(text) is not str:
        raise ValueError('contract lacks expirationDate')
    stamp = datetime.fromisoformat(text)
    if stamp.tzinfo is None:
        raise ValueError('contract expiration lacks a UTC offset')
    return int(stamp.timestamp()) * SECOND_NS


def opening_state(today_response, weekly_response, *, session_date: date, open_ns: int,
                  close_ns: int, rules: dict, today_received_ns: int):
    """Same-day IV proxy plus the fixed weekly call/put pair, or ValueError with a reason."""
    underlying = rules['underlying']
    mid, under_ns = _chain_header(today_response, underlying)
    if under_ns < open_ns:
        raise ValueError('opening underlying quote predates the open')
    pairs = {}
    for side in ('C', 'P'):
        for expiry, leg in _legs(today_response, side, underlying):
            if expiry == session_date:
                strike = _finite(leg.get('strikePrice'), 'strike')
                pairs.setdefault(strike, {})[side] = leg
    complete = [(abs(strike - mid), strike) for strike, legs in pairs.items() if set(legs) == {'C', 'P'}]
    if not complete:
        raise ValueError('no same-day call/put pair for the IV proxy')
    strike = min(complete)[1]
    ivs = []
    for leg in pairs[strike].values():
        if _ms_to_ns(leg.get('quoteTimeInLong'), 'quoteTimeInLong') < open_ns:
            raise ValueError('same-day IV leg quote predates the open')
        iv = _finite(leg.get('volatility'), 'volatility')
        if not 0 < iv <= 500:
            raise ValueError('same-day IV is missing or out of range')
        ivs.append(iv / 100)
    daily = dict(timestamp_ns=under_ns, available_ns=today_received_ns, forward=mid,
                 iv=sum(ivs) / 2, expiry_ns=close_ns)
    if not open_ns <= under_ns <= today_received_ns <= open_ns + rules['opening_deadline_s'] * SECOND_NS:
        raise ValueError('opening snapshot is outside the opening window')

    weekly_mid, weekly_ns = _chain_header(weekly_response, underlying)
    if weekly_ns < open_ns:
        raise ValueError('weekly chain underlying quote predates the open')
    candidates = {}
    for side in ('C', 'P'):
        for expiry, leg in _legs(weekly_response, side, underlying):
            days = (expiry - session_date).days
            if not rules['weekly_min_days'] <= days <= rules['weekly_max_days']:
                continue
            expiry_ns = _expiry_ns(leg)
            if not (expiry_ns > close_ns and 5 * DAY_NS <= expiry_ns - open_ns <= 9 * DAY_NS):
                continue
            strike = _finite(leg.get('strikePrice'), 'strike')
            bid, ask = _finite(leg.get('bid'), 'bid'), _finite(leg.get('ask'), 'ask')
            if not 0 <= bid <= ask or ask <= 0 or _ms_to_ns(leg.get('quoteTimeInLong'), 'quoteTimeInLong') < open_ns:
                continue
            if (side == 'C' and strike < weekly_mid) or (side == 'P' and strike > weekly_mid):
                continue  # out-of-the-money or at-the-money only
            candidates.setdefault(expiry_ns, {}).setdefault(side, []).append(
                (abs(ask - rules['target_opening_ask']), abs(strike - weekly_mid), leg['symbol'], strike, ask, leg))
    usable = sorted(e for e, sides in candidates.items() if set(sides) == {'C', 'P'})
    if not usable:
        raise ValueError('no eligible weekly call and put')
    expiry_ns = usable[0]
    chosen = {side: min(rows)[2:] for side, rows in candidates[expiry_ns].items()}
    return dict(
        opening_daily_iv=daily,
        weekly=dict(call_contract_id=chosen['C'][0], put_contract_id=chosen['P'][0], expiry_ns=expiry_ns),
        contract_terms={side: dict(symbol=row[0], strike=row[1], opening_ask=row[2],
                                   exercise_type=row[3]['exerciseType'], settlement_type='P',
                                   multiplier=100, deliverable=f'100 {underlying}')
                        for side, row in chosen.items()},
        iv_strike=strike,
    )


def find_opening(records, *, session_date, open_ns, close_ns, rules):
    """Scan journaled chain responses in order; the first valid pairing wins."""
    deadline = open_ns + rules['opening_deadline_s'] * SECOND_NS
    today_query, weekly_query = chain_queries(session_date, rules)
    today = weekly = None
    reasons = []
    for record in records:
        if record['event'] not in ('response', 'error') or record['operation'] != 'optionChain':
            continue
        if record['query'] not in (today_query, weekly_query):
            raise JournalError('chain request differs from the protocol opening queries')
        if record['event'] == 'error':
            continue
        if record['received_utc_ns'] > deadline:
            break
        if record['query'] == today_query:
            today = record
        else:
            weekly = record
        if today is None or weekly is None:
            continue
        try:
            state = opening_state(today['response'], weekly['response'], session_date=session_date,
                                  open_ns=open_ns, close_ns=close_ns, rules=rules,
                                  today_received_ns=today['received_utc_ns'])
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            reasons.append(str(error))
            continue
        return dict(state, today_seq=today['seq'], weekly_seq=weekly['seq']), reasons
    return None, reasons


def chain_queries(session_date: date, rules: dict):
    """The two opening requests, identical for collector and builder."""
    common = dict(symbol=rules['underlying'], contractType='ALL', includeUnderlyingQuote=True, strategy='SINGLE')
    today = dict(common, strikeCount=rules['today_strike_count'],
                 fromDate=session_date.isoformat(), toDate=session_date.isoformat())
    weekly = dict(common, strikeCount=rules['weekly_strike_count'],
                  fromDate=(session_date + timedelta(days=rules['weekly_min_days'])).isoformat(),
                  toDate=(session_date + timedelta(days=rules['weekly_max_days'])).isoformat())
    return today, weekly


# ------------------------------------------------------------------- quote admission

def admit_quotes(response, received_ns, symbols, max_age_ms):
    """Yield (symbol, row) or (symbol, reason) for each requested symbol in a quotes response."""
    if type(response) is not dict:
        for symbol in symbols:
            yield symbol, 'malformed_response'
        return
    for symbol in symbols:
        item = response.get(symbol)
        quote = item.get('quote') if type(item) is dict else None
        if type(quote) is not dict:
            yield symbol, 'missing'
            continue
        if item.get('realtime') is not True:
            yield symbol, 'not_realtime'
            continue
        status = quote.get('securityStatus')
        if type(status) is not str or status.lower() != 'normal':
            yield symbol, 'trading_status'
            continue
        bid, ask = quote.get('bidPrice'), quote.get('askPrice')
        bid_size, ask_size = quote.get('bidSize'), quote.get('askSize')
        if (type(bid) not in (int, float) or type(ask) not in (int, float)
                or not math.isfinite(bid) or not math.isfinite(ask) or not 0 <= bid <= ask or ask <= 0
                or type(bid_size) is not int or type(ask_size) is not int or bid_size < 0 or ask_size <= 0
                or (bid > 0) != (bid_size > 0)):
            yield symbol, 'invalid_market'
            continue
        stamp = quote.get('quoteTime')
        if type(stamp) is not int or stamp <= 0:
            yield symbol, 'invalid_source_time'
            continue
        age = received_ns - stamp * 1_000_000
        if age < 0:
            yield symbol, 'source_after_receipt'
            continue
        if age > max_age_ms * 1_000_000:
            yield symbol, 'stale'
            continue
        yield symbol, dict(timestamp_ns=stamp * 1_000_000, available_ns=received_ns, bid=float(bid),
                           ask=float(ask), bid_size=bid_size, ask_size=ask_size)


# ---------------------------------------------------------------------- session build

def build_session(journal: dict, rules: dict):
    """Return ``(session_or_None, diagnostics)`` for one validated journal."""
    start = journal['start']
    if start['rules'] != rules:
        return None, dict(excluded='rules_differ_from_protocol')
    session_date = date.fromisoformat(start['session_date'])
    open_ns, close_ns = session_bounds(session_date, rules['open_et'], start['close_et'])
    records = journal['records']
    diagnostics = dict(session_date=start['session_date'], journal_sha256=journal['sha256'],
                       close_et=start['close_et'], truncated_tail_bytes=journal['truncated_tail_bytes'],
                       errors=sum(r['event'] == 'error' for r in records),
                       resumes=sum(r['event'] == 'resume' for r in records),
                       stop=next((r['reason'] for r in reversed(records) if r['event'] == 'stop'), None))
    opening, reasons = find_opening(records, session_date=session_date, open_ns=open_ns,
                                    close_ns=close_ns, rules=rules)
    if opening is None:
        diagnostics.update(excluded='no_valid_opening_snapshot', opening_rejections=reasons[-5:])
        return None, diagnostics
    recorded = [r for r in records if r['event'] == 'selection']
    expected = {k: opening[k] for k in ('weekly', 'contract_terms', 'today_seq', 'weekly_seq')}
    if not recorded or any({k: r.get(k) for k in expected} != expected for r in recorded):
        raise JournalError('recorded contract selection disagrees with the journaled opening chains')
    weekly = opening['weekly']
    symbols = (rules['underlying'], weekly['call_contract_id'], weekly['put_contract_id'])
    expected_query = dict(symbols=','.join(symbols))
    underlying, options, seen = [], [], set()
    admitted, unavailable = Counter(), Counter()
    poll_times = []
    for record in records:
        if record['event'] not in ('response', 'error') or record['operation'] != 'quotes':
            continue
        if record['query'] != expected_query:
            raise JournalError('quote poll requested symbols other than the selected contracts')
        if record['event'] == 'error':
            unavailable['poll_error'] += len(symbols)
            continue
        poll_times.append(record['received_utc_ns'])
        for symbol, row in admit_quotes(record['response'], record['received_utc_ns'], symbols,
                                        rules['max_quote_age_ms']):
            if type(row) is str:
                unavailable[row] += 1
                continue
            if not open_ns <= row['timestamp_ns'] <= close_ns:
                unavailable['outside_session'] += 1
                continue
            admitted[symbol] += 1
            if symbol == rules['underlying']:
                underlying.append(row)
            elif (symbol, row['timestamp_ns']) not in seen:
                seen.add((symbol, row['timestamp_ns']))
                options.append(dict(contract_id=symbol, timestamp_ns=row['timestamp_ns'],
                                    available_ns=row['available_ns'], bid=row['bid'], ask=row['ask']))
    underlying.sort(key=lambda row: (row['timestamp_ns'], row['available_ns']))
    times = [row['timestamp_ns'] for row in underlying]
    step = rules['bar_interval_s'] * SECOND_NS
    age_limit = rules['max_quote_age_ms'] * 1_000_000
    bars, missing = [], 0
    for grid in range(open_ns, close_ns + 1, step):
        index = bisect_right(times, grid) - 1
        if index < 0 or grid - times[index] > age_limit:
            missing += 1
            continue
        # Among equal event times keep the earliest receipt.
        row = underlying[bisect_left(times, times[index])]
        bars.append(dict(timestamp_ns=grid, available_ns=max(grid, row['available_ns']),
                         close=(row['bid'] + row['ask']) / 2))
    gaps = [b - a for a, b in zip(poll_times, poll_times[1:])]
    diagnostics.update(admitted=dict(admitted), unavailable=dict(unavailable), bars=len(bars),
                       missing_bars=missing, option_quotes=len(options),
                       max_poll_gap_s=max(gaps) / SECOND_NS if gaps else None,
                       contract_terms=opening['contract_terms'], iv_strike=opening['iv_strike'])
    if not bars or not options:
        diagnostics['excluded'] = 'no_admitted_quotes'
        return None, diagnostics
    options.sort(key=lambda row: (row['available_ns'], row['timestamp_ns'], row['contract_id']))
    session = dict(id=start['session_date'], open_ns=open_ns, end_ns=close_ns,
                   opening_daily_iv=opening['opening_daily_iv'], weekly=weekly,
                   bars=bars, option_quotes=options)
    return session, diagnostics


def journal_paths(root: Path, first: date | None = None, last: date | None = None):
    """``root/YYYY-MM-DD/journal.ndjson`` in date order, limited to an inclusive window.

    Directories outside the window are never opened, so a freeze cannot read
    evaluation-period data even by accident.
    """
    found = []
    for child in Path(root).iterdir():
        try:
            day = date.fromisoformat(child.name)
        except ValueError:
            continue
        if child.name != day.isoformat() or (first and day < first) or (last and day > last):
            continue
        path = child / 'journal.ndjson'
        if path.is_file() and not path.is_symlink():
            found.append((day, path))
    return sorted(found)
