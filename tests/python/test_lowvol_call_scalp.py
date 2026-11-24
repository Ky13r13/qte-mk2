import math
import random

import pytest

from qte import lowvol_call_scalp as s


def ctx(regime="low_vol_trend"):
    return dict(ratio=10.0, vix=13.0, em=600.0 * 13 / 100 / math.sqrt(252) * 10, regime=regime)


def session(closes, start=570):
    """5-minute bars (minute, open, high, low, close) from closes; SPY-scale prices."""
    bars, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        bars.append((start + 5 * i, o, max(o, c) + 0.01, min(o, c) - 0.01, c))
        prev = c
    return bars


def test_pricing_and_adverse_rounding():
    assert s.call_mid(6000, 6005, 0, 0.13) == 0.0
    assert s.call_mid(6010, 6005, 0, 0.13) == 5.0
    assert s.call_mid(6000, 6005, 180, 0.13) > s.call_mid(6000, 6005, 30, 0.13) > 0
    assert s.buy_price(1.02, 0.05) == pytest.approx(1.10)
    assert s.sell_price(1.02, 0.05) == pytest.approx(0.95)
    assert s.sell_price(0.02, 0.05) == 0.0


def test_r5_signal_needs_pullback_then_reversal_inside_the_window():
    closes = [600.0] * 30 + [600.0, 599.9, 599.8, 599.7, 599.6, 599.5, 599.4, 599.7] + [599.7] * 40
    bars = session(closes)
    c = ctx()
    sig = s.signals(bars, c, "R5", calm=False)
    window = [t for t in sig if 720 <= bars[t][0] + 5 <= 900]
    assert sig == window  # nothing outside 12:00-15:00 ET
    morning = s.signals(session(closes, start=570), dict(c), "R5", calm=False)
    shifted = s.signals(session([600.0] * 30 + closes), dict(c), "R5", calm=False)
    assert all(720 <= session([600.0] * 30 + closes)[t][0] + 5 <= 900 for t in shifted)
    assert morning == sig


def test_trade_uses_next_bar_open_and_exits_on_time_without_lookahead():
    bars = session([600.0] * 78)
    c = ctx()
    t = 40
    p = dict(s.PRIMARY, hold=15)
    tr = s.trade_from(bars, c, t, p)
    assert tr["entry_min"] == bars[t + 1][0]
    assert tr["strike"] == 6005 and tr["reason"] == "time"
    assert tr["exit_min"] - tr["entry_min"] == 15
    assert tr["pnl"] < 0  # flat price: theta + spread + fees
    future = list(bars)
    future[-1] = (future[-1][0], 600.0, 700.0, 600.0, 700.0)  # a huge move after the exit
    assert s.trade_from(future, c, t, p) == tr


def test_run_filters_regime_and_limits_two_trades():
    bars = session([600.0] * 78)
    sessions = {"2020-01-02": bars}
    out = s.run(sessions, {"2020-01-02": ctx("trend_up")}, s.PRIMARY, rng=random.Random(0))
    assert out == []
    out = s.run(sessions, {"2020-01-02": ctx()}, dict(s.PRIMARY, calm=False), rng=random.Random(0))
    assert 1 <= len(out) <= 2


def test_study_never_reads_past_validation_end(monkeypatch, tmp_path):
    import json
    split = tmp_path / "split.json"
    split.write_text(json.dumps({"validation_end": "2016-01-05", "dev_end": "2016-01-04"}))
    monkeypatch.setattr(s, "SPLIT", split)
    seen = {}

    def fake_load(db, *, last_session):
        seen["last"] = last_session
        return {"2016-01-04": session([200.0] * 78)}, {"2016-01-04": ctx()}
    monkeypatch.setattr(s, "load", fake_load)
    report = s.study(None, seeds=1)
    assert seen["last"] == "2016-01-05" and report["last"] <= "2016-01-05"
