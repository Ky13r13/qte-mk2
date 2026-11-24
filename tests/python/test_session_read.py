from datetime import datetime
from hashlib import sha256
import sqlite3
from pathlib import Path

import pytest

from qte import _core, regime_lab, session_read, session_options, truth_db


def test_calendar_time_mid_and_position():
    captured = datetime.fromisoformat("2026-11-24T12:20:45.277-05:00")
    at = int(captured.timestamp()*10**9)
    expiry = int(datetime.fromisoformat("2026-11-24T16:00:00-05:00").timestamp()*10**9)
    option = dict(symbol="SPXW  261124C07200000", strike=7200, right="C", expiry="2026-11-24",
                  expiry_ns=expiry, volatility=33.116, bid=19.9, ask=20.1, quoteTimeInLong=at//10**6)
    result = session_options.position("2x SPXW 7200C @19.50", [option], 7198.38, at, "2026-11-24")
    assert result["mark"] == 20 and result["pnl_usd"] == 100
    assert result["expiry_breakeven"] == 7219.5
    price, _ = _core.european_option(7198.38, 6970, .57377, (expiry-at)/session_options.YEAR, False)
    assert abs(price-.075)*100 <= 1
    assert result["grid"][2]["pnl_usd"][-1] == -3900


def test_prior_features_loads_only_through_prior(monkeypatch):
    from datetime import date, timedelta
    asof = "2026-11-23"
    dates = [(date.fromisoformat(asof)-timedelta(days=300-i)).isoformat() for i in range(301)]
    calls = []

    def daily(db, symbol, *, end):
        calls.append(end)
        return {k: dict(close=7000+i if symbol in ("SPX", "SPY") else 20 if symbol == "VIX" else 21,
                        high=7500, low=6000, volume=100) for i, k in enumerate(dates)}
    monkeypatch.setattr(truth_db, "daily", daily)
    features = regime_lab.preopen_row(None, asof)
    assert calls == [asof]*4 and features["anchor"] == 7300
    assert features["vix_ts"] == 20/21


def test_replay_from_small_capture(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from qte.adapters import schwab_session as adapter
    opened = int(datetime.fromisoformat("2026-11-24T09:30:00-05:00").timestamp())*10**9
    at = opened+31*adapter.MINUTE

    def save(name, symbol, op, params, response):
        digest = sha256(truth_db._canonical(response)).hexdigest()
        p = tmp_path/(name+".json")
        p.write_text(json.dumps(dict(provider="schwab", request=dict(operation_id=op, query_parameters=dict(params, symbol="$"+symbol)),
                                     request_started_utc_ns=at-1, response_received_utc_ns=at,
                                     response_canonical_json_sha256=digest, response=response)))
    save("spx-chain-0dte", "SPX", "optionChain", {}, dict(underlyingPrice=7002,
         isDelayed=False, callExpDateMap={}, putExpDateMap={}))
    for sym, px in (("SPX", 7000), ("VIX", 20), ("VIX3M", 25)):
        rows = [dict(datetime=(opened+i*5*adapter.MINUTE)//10**6, open=px,
                     high=px+1, low=px-1, close=px) for i in range(7)]
        rows[-1].update(high=9999)
        save(sym.lower()+"-5m", sym, "priceHistory", dict(frequency=5, frequencyType="minute",
             needExtendedHoursData=False, endDate=at//10**6), dict(candles=rows))
    monkeypatch.setattr(regime_lab, "preopen_row", lambda db, date: dict(
        asof=date, anchor=7000, em=100, vix=20, vix_ts=.8))
    result = session_read.summarize(tmp_path, None, dict(regimes=[dict(id="R01", rules=["vix_ts <= 0.84"])]))
    assert result["regime"] == "R01"
    assert result["high_sigma"] == .01 and result["session_bars"] == 6
    assert result["volatility"]["term_structure"] == .8
    assert result["gate"]["score"] is None
