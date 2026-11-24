"""Descriptive, overlapping 30-calendar-day bankroll replays for one frozen rule."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from statistics import median

WINDOW_DAYS = 30
INITIAL_CENTS = 1_000_000
TARGET_CENTS = 5 * INITIAL_CENTS
DEFAULT_FRACTIONS = (.02, .05, .10, .20, .30)


def _date(text):
    day = date.fromisoformat(text)
    if day.isoformat() != text:
        raise ValueError('Use YYYY-MM-DD for calendar days')
    return day


def _decimal(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('Expected a finite decimal number') from exc
    if not number.is_finite():
        raise ValueError('Expected a finite decimal number')
    return number


def _cents(value):
    number = _decimal(value)
    if number < 0:
        raise ValueError('Use nonnegative debit and liquidation proceeds in dollars')
    return int((number * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _replay(start, observed, fraction):
    cash = peak = INITIAL_CENTS
    drawdown = 0.0
    crossings = None
    count = no_trade = unavailable = no_cash = risk_cap = contracts = 0
    for i in range(WINDOW_DAYS):
        day = start + timedelta(days=i)
        if day not in observed:
            unavailable += 1
            continue
        terms = observed[day]
        if terms is None:
            no_trade += 1
            continue
        debit, proceeds = terms
        if cash < debit:
            no_cash += 1
            continue
        quantity = int(cash * fraction) // debit
        if quantity == 0:
            risk_cap += 1
            continue
        count += 1
        contracts += quantity
        cash += quantity * (proceeds - debit)
        peak = max(peak, cash)
        drawdown = max(drawdown, (peak - cash) / peak)
        if cash >= TARGET_CENTS and crossings is None:
            crossings = day.isoformat()

    return {
        'start_date': start.isoformat(),
        'end_date': (start + timedelta(days=WINDOW_DAYS - 1)).isoformat(),
        'complete': unavailable == 0,
        'first_target_date': crossings,
        'ending_equity_dollars': cash / 100,
        'peak_equity_dollars': peak / 100,
        'max_closed_equity_drawdown_fraction': drawdown,
        'executed_trades': count,
        'contracts': contracts,
        'known_no_trade_days': no_trade,
        'missing_days': unavailable,
        'skipped_insufficient_cash': no_cash,
        'skipped_below_one_contract_at_risk_cap': risk_cap,
        'zero_equity': cash == 0,
    }


def _summary(windows):
    complete = [row for row in windows if row['complete']]
    endings = [row['ending_equity_dollars'] for row in complete]
    return {
        'window_count': len(windows),
        'complete_window_count': len(complete),
        'incomplete_window_count': len(windows) - len(complete),
        'target_hit_windows': sum(row['first_target_date'] is not None for row in complete),
        'target_terminal_windows': sum(value >= TARGET_CENTS / 100 for value in endings),
        'median_terminal_equity_dollars': median(endings) if endings else None,
        'worst_terminal_equity_dollars': min(endings) if endings else None,
        'best_terminal_equity_dollars': max(endings) if endings else None,
        'worst_closed_equity_drawdown_fraction': max(
            (row['max_closed_equity_drawdown_fraction'] for row in complete), default=None),
        'windows_with_insufficient_cash': sum(row['skipped_insufficient_cash'] > 0 for row in complete),
        'windows_with_budget_below_one_contract': sum(
            row['skipped_below_one_contract_at_risk_cap'] > 0 for row in complete),
        'zero_equity_windows': sum(row['zero_equity'] for row in complete),
    }


def rolling_windows(calendar_start, last_date, trades_by_date, fractions=DEFAULT_FRACTIONS):
    """Return scenario summaries and every complete-length calendar window.

    A date mapped to None is a known no-trade day, including declared market
    holidays and weekends. Omitted dates are unknown. Incomplete windows show
    observed-path equity only and stay outside the summary outcome counts.
    Each priced observation represents one non-overlapping position for the
    same frozen rule. Debit includes entry fees and any reserved closing fee.
    Include fees exactly once per round trip. Liquidation proceeds must be
    nonnegative. Use zero for a modeled full loss of the reserved debit.
    """
    first, last = _date(calendar_start), _date(last_date)
    if last < first:
        raise ValueError('Last date must be on or after calendar start')
    observed = {}
    for key, trade in trades_by_date.items():
        day = _date(key)
        if not first <= day <= last:
            raise ValueError(f'Observation outside calendar: {key}')
        if trade is None:
            observed[day] = None
            continue
        debit = _cents(trade['debit_dollars'])
        if debit <= 0:
            raise ValueError(f'Risk per contract must be positive: {key}')
        observed[day] = debit, _cents(trade['proceeds_dollars'])

    scenarios = []
    count = max(0, (last - first).days - WINDOW_DAYS + 2)
    for raw in fractions:
        fraction = _decimal(raw)
        if not 0 < fraction <= 1:
            raise ValueError('Debit fractions must be above zero and at most one')
        windows = [_replay(first + timedelta(days=i), observed, fraction) for i in range(count)]
        scenarios.append({'debit_fraction': float(fraction), 'summary': _summary(windows), 'windows': windows})
    return {'window_calendar_days': WINDOW_DAYS, 'initial_equity_dollars': INITIAL_CENTS / 100,
            'target_equity_dollars': TARGET_CENTS / 100, 'scenarios': scenarios}
