import pytest

from qte.research_triage import compare, concentration


def trade(day, pnl, scenario="base", **extra):
    return dict(date=day, pnl=pnl, scenario=scenario, direction=1,
                regime="range", **extra)


def test_concentration_removes_whole_session_not_one_winning_trade():
    result = concentration([trade("2025-01-02", 10), trade("2025-01-02", 5),
                            trade("2025-01-03", -4)])
    assert result["pnl"] == 11
    assert result["sessions"] == 2
    assert result["best_day_pnl"] == 15
    assert result["pnl_excluding_best_day"] == -4
    assert concentration([])["best_day"] is None
    with pytest.raises(ValueError, match="finite"):
        concentration([trade("2025-01-02", float("nan"))])


def test_scenario_comparison_exposes_unmatched_trades_and_absent_scenarios():
    trades = [trade("2025-01-02", 10), trade("2025-01-03", 100),
              trade("2025-01-02", 6, "wide")]
    result = compare(trades, ("base", "wide"))
    assert result["common_trades"] == 1
    assert result["scenarios"]["base"]["all_recorded"]["pnl"] == 110
    assert result["scenarios"]["base"]["matched"]["pnl"] == 10
    assert result["scenarios"]["base"]["not_in_common"] == 1
    assert result["scenarios"]["wide"]["matched"]["pnl"] == 6
    assert compare(trades[:2], ("base", "wide"))["common_trades"] == 0


def test_conflicting_contracts_and_duplicate_intents_fail():
    with pytest.raises(ValueError, match="conflicting"):
        compare([trade("2025-01-02", 10, strike=5000),
                 trade("2025-01-02", 6, "wide", strike=5005)], ("base", "wide"))
    with pytest.raises(ValueError, match="Duplicate"):
        compare([trade("2025-01-02", 10)]*2, ("base", "wide"))
