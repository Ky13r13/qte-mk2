import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from qte import day_runner as dr, orb_forward as of

NY = ZoneInfo("America/New_York")
MIN = of.MINUTE
DAY, _ = of.session(int(datetime(2026, 11, 24, 12, tzinfo=NY).timestamp()) * 10**9)


def snap(closes_by_minute, spot, call=(9.8, 10.2), put=(9.6, 10.0)):
    rows = [dict(datetime=(DAY.open_ns + m * MIN) // 10**6, open=c, high=c + 1, low=c - 1, close=c, volume=1)
            for m, c in closes_by_minute.items()]
    strike = 5 * round(spot / 5)

    def side(bid, ask, right):
        return {"2026-11-24:0": {f"{strike:.1f}": [dict(symbol=f"SPXW  261124{right}0{strike*1000:07d}", bid=bid,
                                                        ask=ask, settlementType="P", multiplier=100)]}}
    return {"spx-5m": dict(response=dict(candles=rows)),
            "spx-chain-0dte": dict(response={"underlyingPrice": spot, "callExpDateMap": side(*call, "C"),
                                             "putExpDateMap": side(*put, "P")})}


def test_close_entry_uses_completed_bars_and_atm_asks():
    closes = {m: 7000.0 for m in range(0, 390, 5)}
    closes[345] = 6990.0   # bar 15:15-15:20
    closes[375] = 7004.0   # bar 15:45-15:50
    now = DAY.close_ns - 10 * MIN + 30 * 10**9   # 15:50:30
    e = dr.close_entry(snap(closes, 7003.0), DAY, now)
    assert e["status"] == "captured" and e["strike"] == 7005
    assert e["momentum_points"] == 14.0 and e["momentum_side"] == 1
    assert e["straddle_ask"] == pytest.approx(20.2) and e["implied_abs_move"] == pytest.approx(19.8)
    early = dr.close_entry(snap(closes, 7003.0), DAY, DAY.close_ns - 20 * MIN)   # 15:50 bar not complete
    assert early["status"] == "incomplete"


def test_close_settle_cash_settles_each_leg_with_one_fee():
    entry = dict(strike=7005, spot=7003.0, implied_abs_move=19.8, momentum_side=1,
                 call=dict(ask=10.2), put=dict(ask=10.0))
    out = dr.close_settle(entry, 7030.0)
    assert out["call_pnl_usd"] == pytest.approx((25 - 10.2) * 100 - 1.25)
    assert out["put_pnl_usd"] == pytest.approx(-10.0 * 100 - 1.25)
    assert out["straddle_pnl_usd"] == pytest.approx(out["call_pnl_usd"] + out["put_pnl_usd"])
    assert out["momentum_pnl_usd"] == out["call_pnl_usd"] and out["realized_abs_move"] == 27.0


def bars(path):
    return [dict(open=o, high=max(o, c) + 1, low=min(o, c) - 1, close=c) for o, c in path]


def test_day_outcome_labels_trend_reversal_and_range():
    em = 7000 * 0.16 / 15.8745
    up = bars([(7000 + i * 2, 7000 + (i + 1) * 2) for i in range(78)])
    o = dr.day_outcome(up, 7000.0, 16.0)
    assert "trend_up_day" in o["labels"] and o["range_sigma"] > 1 and o["expected_move"] == pytest.approx(em, abs=0.01)
    v = bars([(7000, 6880)] + [(6880, 6880)] * 30 + [(6880, 7000)] + [(7000, 7000)] * 40)
    assert "reversal_from_low" in dr.day_outcome(v, 7000.0, 16.0)["labels"]
    quiet = bars([(7000, 7001)] * 78)
    assert dr.day_outcome(quiet, 7000.0, 16.0)["labels"] == ["range_day"]


def test_render_is_readable_with_partial_data():
    rep = dict(date="2026-11-24",
               session=dict(labels=["range_day"], open=7000, close=7010, close_sigma=0.1, range_sigma=0.5,
                            gap_sigma=0.0, last10_sigma=0.4, expected_move=70.5, prior_vix=16.0),
               regime_today=dict(regime="R03", range_vs_regime_mean=0.64),
               regime_tomorrow=dict(regime=None, reason="today's final daily close not yet in the truth database"),
               orb_trial=dict(today={}, running=dict(trades=0, total_pnl_usd=0, decision="pending")),
               close_study=dict(today=dict(entry=None, settle=None), running=dict(sessions=0)))
    text = dr.render(rep)
    assert "range_day" in text and "R03" in text and "not yet in the truth database" in text
    assert "Early-ORB paper trial" in text and "15:50 close study" in text


def test_gap_days_are_labeled():
    gap = bars([(7150, 7152)] + [(7152, 7151)] * 77)
    labels = dr.day_outcome(gap, 7000.0, 16.0)["labels"]
    assert labels[0] == "gap_up_day" and "range_day" in labels
