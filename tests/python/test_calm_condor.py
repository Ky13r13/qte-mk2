import math

import pytest

from qte import calm_condor as cc


def ctx(vix=13.0):
    return dict(ratio=10.0, vix=vix, em=600 * vix / 100 / math.sqrt(252) * 10, regime="trend_up")


def flat_session(open_price=600.0, settle=600.0, path=None):
    bars = []
    for i, m in enumerate(range(570, 960, 5)):
        c = (path or {}).get(m, open_price if m < 955 else settle)
        bars.append((m, c, c + 0.01, c - 0.01, c))
    return bars


def test_fills_round_against_the_trader():
    assert cc.sell_fill(1.02, 0.05) == pytest.approx(0.95)
    assert cc.buy_fill(1.02, 0.05) == pytest.approx(1.10)
    assert cc.buy_fill(0.0, 0.05) == pytest.approx(0.05)
    assert cc.sell_fill(0.02, 0.05) == 0.0


def test_condor_strikes_are_symmetric_in_expected_moves():
    legs = cc.structure_legs(6000.0, 360, 0.13, cc.PRIMARY)
    (q1, k1, sp), (q2, k2, lp), (q3, k3, sc), (q4, k4, lc) = legs
    em = 6000 * 0.13 * math.sqrt(360 / (390 * 252))
    assert (q1, k1, q2, k2, q3, k3, q4, k4) == (-1, "put", 1, "put", -1, "call", 1, "call")
    assert sp == 5 * math.floor((6000 - em) / 5) and sc == 5 * math.ceil((6000 + em) / 5)
    assert sp - lp == 25 and lc - sc == 25


def test_settlement_inside_keeps_credit_and_beyond_wing_loses_the_bound():
    calm = cc.trade_day(flat_session(), ctx(), cc.PRIMARY)
    assert calm["reason"] == "settle" and calm["settle"] == 6000.0
    assert calm["pnl"] == pytest.approx(calm["credit"] * 100 - 4 * cc.FEE)
    crash = cc.trade_day(flat_session(settle=590.0), ctx(), cc.PRIMARY)  # SPX -100 at the close
    assert crash["pnl"] == pytest.approx(-crash["max_loss"])
    assert crash["max_loss"] == pytest.approx((25 - crash["credit"]) * 100 + 4 * cc.FEE)


def test_stop_closes_on_the_first_close_beyond_a_short_strike():
    p = dict(cc.PRIMARY, stop=True)
    path = {m: 600.0 for m in range(570, 960, 5)}
    path[700] = 590.0  # 11:40 ET close far below the short put
    out = cc.trade_day(flat_session(path=path), ctx(), p)
    assert out["reason"] == "stop" and out["pnl"] < 0


def test_missing_entry_or_settlement_bar_skips_the_day():
    bars = [b for b in flat_session() if b[0] != 600]
    assert cc.trade_day(bars, ctx(), cc.PRIMARY) is None
    bars = [b for b in flat_session() if b[0] != 955]
    assert cc.trade_day(bars, ctx(), cc.PRIMARY) is None


def test_iv_check_reports_short_strike_iv_relative_to_vix():
    def q(iv):
        return [dict(volatility=iv, bid=1.0, ask=1.2)]
    chain = {"underlyingPrice": 6000.0,
             "putExpDateMap": {"e:0": {"6000.0": q(13.0), "5950.0": q(15.6), "5900.0": q(18.0)}},
             "callExpDateMap": {"e:0": {"6000.0": q(13.0), "6050.0": q(11.7), "6100.0": q(11.0)}}}
    # 13% calendar IV, 2,360 minutes: remaining 1σ = 6000 x 0.13 x sqrt(2360/525600) ≈ 52 pts
    out = cc.iv_check(chain, 13.0, minutes_left=2360)
    put, call = out["expiries"]["e:0"]["put"], out["expiries"]["e:0"]["call"]
    assert put["atm_iv_calendar_over_vix"] == 1.0 and put["short_strike"] == 5950.0
    assert put["short_iv_calendar_over_vix"] == 1.2 and call["short_strike"] == 6050.0
    assert put["atm_model_multiple"] == pytest.approx(0.432, abs=0.001)  # calendar -> trading-time scale
    assert call["short_model_multiple"] == pytest.approx(0.9 * 0.4324, abs=0.001)


def test_calendar_to_trading_conversion_matches_a_real_quote():
    """Regression 2026-11-24: Schwab 0DTE IV is calendar time. SPXW 7170P at 12:26 ET, SPX 7197.21,
    IV 34.7%, 213 minutes to the close, market mid $10.00."""
    from qte.gamma_surface import _black76
    calendar = _black76(7197.21, 7170, 0.347, 213 / 525_600, 0, "put")["price"]
    trading = _black76(7197.21, 7170, 0.347 * cc.CALENDAR_TO_TRADING, 213 / (390 * 252), 0, "put")["price"]
    assert calendar == pytest.approx(10.0, abs=1.0) and trading == pytest.approx(calendar, rel=1e-9)


def test_native_condor_matches_reference_across_exits_and_costs():
    from qte.calm_condor import PRIMARY, trade_day, trade_day_native
    for direction in (-1, 0, 1):
        bars = [(600, 700., 700., 700., 700.), (605, 700., 730., 670., 700.+30*direction),
                (955, 700., 730., 670., 700.+30*direction)]
        for structure in ("condor", "put_spread"):
            for stop in (False, True):
                params = dict(PRIMARY, structure=structure, stop=stop, half_spread=.10)
                context = dict(ratio=10., vix=21.)
                assert trade_day_native(bars, context, params) == trade_day(bars, context, params)
    assert trade_day_native(bars[:-1], context, params) is None
