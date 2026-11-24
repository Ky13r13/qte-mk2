"""Deterministic Schwab-shaped market for forward collector, protocol and ledger tests.

The real ``ForwardCollector`` runs against a fake client and a simulated clock.
Payload shapes follow captured Schwab ``optionChain`` and ``quotes`` responses.
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone
import math

from qte import forward_collector as fc
from qte.forward_sessions import DEFAULT_RULES, NY, SECOND_NS

RULES = dict(DEFAULT_RULES, close_et='10:30', allowed_close_et=['10:30', '13:00'])
LATENCY_NS = 150_000_000
NS_PER_MS = 1_000_000


def _ms(ns):
    return ns // NS_PER_MS


def option_symbol(root, expiry: date, side, strike):
    return f'{root:<6}{expiry:%y%m%d}{side}{int(round(strike * 1000)):08d}'


class FakeMarket:
    """Underlying oscillates around 600 with fixed amplitude/period; options move with delta 0.5."""

    def __init__(self, session_date: date, *, amplitude=2.0, period_s=1200, drift_per_hour=0.0,
                 flat=False, fail_polls=()):
        self.day = session_date
        self.open_ns = int(datetime.combine(session_date, dtime(9, 30), NY).astimezone(timezone.utc).timestamp()) * SECOND_NS
        self.amplitude, self.period_s, self.drift = amplitude, period_s, drift_per_hour
        self.flat = flat
        self.weekly_expiry = session_date + timedelta(days=7)
        self.fail_polls = set(fail_polls)
        self.polls = 0
        self.now = self.open_ns - 60 * SECOND_NS
        self.contract_sha256 = 'f' * 64

    # simulated clock
    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(1, int(seconds * SECOND_NS))

    def spot(self, at_ns):
        elapsed = (at_ns - self.open_ns) / SECOND_NS
        if self.flat:
            return 600.0
        return 600.0 + self.amplitude * math.sin(2 * math.pi * elapsed / self.period_s) + self.drift * elapsed / 3600

    def option_mid(self, side, strike, at_ns):
        s = self.spot(at_ns)
        return max(0.05, 3.0 + 0.5 * ((s - strike) if side == 'C' else (strike - s)))

    def _leg(self, side, strike, expiry, at_ns):
        mid = self.option_mid(side, strike, at_ns)
        expiry_text = datetime.combine(expiry, dtime(21, 0)).isoformat() + '.000+00:00'
        return dict(symbol=option_symbol('SPY', expiry, side, strike), putCall='CALL' if side == 'C' else 'PUT',
                    multiplier=100.0, nonStandard=False, mini=False, settlementType='P', exerciseType='A',
                    optionDeliverablesList=[dict(assetType='STOCK', currencyType=None, deliverableUnits=100.0,
                                                 symbol='SPY')],
                    strikePrice=float(strike), bid=round(mid - 0.02, 2), ask=round(mid + 0.02, 2),
                    quoteTimeInLong=_ms(at_ns) - 50, volatility=15.0, expirationDate=expiry_text)

    def _chain(self, query, at_ns):
        expiry = date.fromisoformat(query['fromDate'])
        strikes = (599.0, 600.0, 601.0)
        maps = {}
        for side, key in (('C', 'callExpDateMap'), ('P', 'putExpDateMap')):
            dte = (expiry - self.day).days
            maps[key] = {f'{expiry.isoformat()}:{dte}': {f'{k:.1f}': [self._leg(side, k, expiry, at_ns)]
                                                         for k in strikes}}
        s = self.spot(at_ns)
        return dict(status='SUCCESS', symbol='SPY', isDelayed=False,
                    underlying=dict(symbol='SPY', delayed=False, bid=round(s - 0.01, 2), ask=round(s + 0.01, 2),
                                    quoteTime=_ms(at_ns) - 50), **maps)

    def _quotes(self, query, at_ns):
        out = {}
        for symbol in query['symbols'].split(','):
            if symbol == 'SPY':
                s = self.spot(at_ns)
                bid, ask = round(s - 0.01, 2), round(s + 0.01, 2)
            else:
                side, strike = symbol[12], int(symbol[13:]) / 1000
                mid = self.option_mid(side, strike, at_ns)
                bid, ask = round(mid - 0.02, 2), round(mid + 0.02, 2)
            out[symbol] = dict(realtime=True, symbol=symbol,
                               quote=dict(securityStatus='Normal', bidPrice=bid, askPrice=ask, bidSize=10,
                                          askSize=10, quoteTime=_ms(at_ns) - 50))
        return out

    # client interface used by ForwardCollector
    def get(self, operation, *, query_parameters):
        served = self.now + LATENCY_NS // 2
        self.now += LATENCY_NS
        if operation == 'optionChain':
            return self._chain(query_parameters, served)
        self.polls += 1
        if self.polls in self.fail_polls:
            raise OSError('simulated network failure')
        return self._quotes(query_parameters, served)


def collect(journal_root, session_date: date, *, close_et='10:30', rules=RULES, interval_s=10, **market):
    """Run the real collector for one simulated session and return (summary, market)."""
    market = FakeMarket(session_date, **market)
    directory = journal_root / session_date.isoformat()
    directory.mkdir(parents=True, exist_ok=True)
    journal = fc.Journal(directory / 'journal.ndjson')
    try:
        collector = fc.ForwardCollector(journal, lambda: market, session_date=session_date, rules=rules,
                                        close_et=close_et, interval_ns=interval_s * SECOND_NS,
                                        clock=market.clock, sleep=market.sleep)
        summary = collector.run()
    finally:
        journal.close()
    return summary, market
