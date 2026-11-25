from collections import deque

import pytest

from qte import orb_options as o


def ctx():
    return dict(ratio=10.0, vix=15.0, em=6000 * 0.15 / 15.874)


def bars(path):
    out, prev = [], path[0]
    for i, c in enumerate(path):
        out.append((570 + 5 * i, prev, max(prev, c) + 0.01, min(prev, c) - 0.01, c))
        prev = c
    return out


def test_tick_rules_and_adverse_rounding():
    assert o.buy_fill(2.92, 0.05) == pytest.approx(3.0)      # 2.97 rounds up on the $0.05 grid
    assert o.buy_fill(3.02, 0.05) == pytest.approx(3.1)      # >= $3 uses $0.10 ticks
    assert o.sell_fill(3.12, 0.05) == pytest.approx(3.0)
    assert o.sell_fill(0.02, 0.05) == 0.0


def test_signal_needs_narrow_range_breakout_and_prior_volume_baseline():
    b = bars([600.0, 600.1] + [600.05] * 4 + [600.5] + [600.5] * 70)
    vol = {m: 100.0 for m, *_ in b}
    vol[600] = 500.0                                          # the breakout bar (index 6 -> minute 600)
    base = {m: deque([100.0] * 20, maxlen=20) for m, *_ in b}
    t, side, hi, lo = o.signal(b, ctx(), vol, base, o.PRIMARY)
    assert b[t][0] == 600 and side == 1
    weak = {**vol, 600: 150.0}
    assert o.signal(b, ctx(), weak, base, o.PRIMARY) is None   # volume not > 2x
    short = {m: deque([100.0] * 5, maxlen=20) for m in base}
    assert o.signal(b, ctx(), vol, short, o.PRIMARY) is None   # needs 20 prior sessions


def test_trade_enters_next_open_and_stops_at_range_midpoint():
    b = bars([600.0, 600.1, 600.6, 600.7, 600.0] + [600.0] * 70)
    tr = o.trade(b, ctx(), 2, 1, 600.11, 599.99, o.PRIMARY)
    assert tr["entry_min"] == b[3][0] and tr["reason"] == "stop" and tr["exit_min"] == b[4][0] + 5
    assert tr["pnl"] < 0


def test_baseline_updates_only_after_the_day_so_no_lookahead():
    day = bars([600.0, 600.1] + [600.05] * 4 + [600.5] + [600.5] * 70)
    sessions = {f"2020-01-{k + 1:02d}": day for k in range(22)}
    ctxs = {d: ctx() for d in sessions}
    vols = {(d, m): 100.0 for d in sessions for m, *_ in day}
    vols[("2020-01-22", 600)] = 500.0
    trades = o.run(sessions, ctxs, vols, o.PRIMARY)
    assert [t["date"] for t in trades] == ["2020-01-22"]       # first 20 days build the baseline only
