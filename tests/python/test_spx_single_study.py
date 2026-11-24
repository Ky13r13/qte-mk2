from datetime import date, datetime, time, timedelta

import pytest

from qte.spx_single_study import FIVE_MIN_NS, NY, _quantity, study

MINUTE = FIVE_MIN_NS // 5


@pytest.mark.parametrize("direction, right", [(-1, "call"), (1, "put")])
def test_models_next_minute_entry_and_five_minute_exit_on_known_prior_vol(direction, right):
    day = date(2026, 11, 5)
    start = int(datetime.combine(day, time(9, 30), NY).timestamp()) * 1_000_000_000
    prices = {5: 6000 + direction * 37, 10: 6000 + direction * 23,
              11: 6000 + direction * 22, 16: 6000 + direction * 15}
    bars = [{"timestamp_ns": start + i * MINUTE, "available_ns": start + i * MINUTE,
             "open": 6000, "close": prices.get(i, 6000)} for i in range(1, 391)]
    sessions = [{"id": str(day), "open_ns": start, "end_ns": start + 390 * MINUTE, "bars": bars}]
    prior = day - timedelta(days=1)
    daily = {prior: .05, day: .8}
    weekly = {prior: .2, day: .8}
    report = study(sessions, daily, weekly, {})
    assert report["sessions"][0]["daily_vol"] == .05
    assert report["sessions"][0]["weekly_vol"] == .2
    assert report["news_coverage"] == "unavailable"
    by_id = {(row["scenario"], row["sizing"]): row for row in report["candidates"]}
    single = by_id["base", "1 contract"]
    assert single["summary"]["entries"] == 1
    trade = single["trades"][0]
    assert trade["side"] == right
    assert trade["entry_ns"] == start + 11 * MINUTE
    assert trade["exit_ns"] - trade["entry_ns"] == FIVE_MIN_NS
    assert trade["risk_reserved"] == trade["entry_debit"] + 1.25
    assert trade["net_pnl"] == pytest.approx(trade["exit_premium_points"] * 100 - 1.25 - trade["entry_debit"])
    five = by_id["base", "5 contracts"]
    assert five["summary"]["net_pnl"] == 5 * trade["net_pnl"]
    assert five["session_equity"][-1]["equity"] == 100_000 + five["summary"]["net_pnl"]
    assert by_id["base", "2% risk budget"]["skips"] == {"budget_below_one_contract": 1}
    assert by_id["wide costs and IV drop", "1 contract"]["summary"]["net_pnl"] < trade["net_pnl"]


def test_whole_contract_sizing_respects_cents_and_cash():
    assert _quantity(100_000, 20_000, 5, 0) == (5, None)
    assert _quantity(99_999, 20_000, 5, 0) == (0, "cash")
    assert _quantity(100_000, 4_000, 0, 800) == (2, None)
    assert _quantity(99_999, 4_000, 0, 800) == (1, None)
    assert _quantity(100_000, 8_001, 0, 800) == (0, "budget_below_one_contract")
    assert _quantity(100_000, 1_000, 0, 800) == (5, None)
