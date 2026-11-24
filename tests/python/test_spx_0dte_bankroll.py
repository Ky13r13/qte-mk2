import pytest

from qte.spx_0dte_bankroll import rolling_windows


def test_calendar_windows_distinguish_unknown_from_known_no_trade():
    observed = {f'2026-10-{i:02}': None for i in range(1, 31)}
    observed['2026-10-01'] = {'debit_dollars': 300, 'proceeds_dollars': 450}
    report = rolling_windows('2026-10-01', '2026-10-31', observed, fractions=(.10,))
    scenario = report['scenarios'][0]
    first, second = scenario['windows']
    assert first['contracts'] == 3
    assert first['ending_equity_dollars'] == 10450
    assert first['known_no_trade_days'] == 29
    assert first['complete'] and not second['complete']
    assert second['missing_days'] == 1
    assert scenario['summary']['complete_window_count'] == 1
    assert scenario['summary']['median_terminal_equity_dollars'] == 10450


def test_target_hit_and_terminal_target_differ_under_later_losses():
    observed = {f'2026-10-{i:02}': None for i in range(1, 31)}
    observed['2026-10-01'] = {'debit_dollars': 10_000, 'proceeds_dollars': 50_000}
    observed['2026-10-02'] = {'debit_dollars': 50_000, 'proceeds_dollars': 0}
    observed['2026-10-03'] = {'debit_dollars': 1, 'proceeds_dollars': 1}
    scenario = rolling_windows('2026-10-01', '2026-10-30', observed, fractions=(1,))['scenarios'][0]
    assert scenario['summary']['target_hit_windows'] == 1
    assert scenario['summary']['target_terminal_windows'] == 0
    assert scenario['summary']['zero_equity_windows'] == 1
    assert scenario['windows'][0]['max_closed_equity_drawdown_fraction'] == 1
    assert scenario['windows'][0]['skipped_insufficient_cash'] == 1


def test_risk_cap_can_block_entry_without_cash_shortage():
    observed = {'2026-10-01': {'debit_dollars': 201, 'proceeds_dollars': 400}}
    row = rolling_windows('2026-10-01', '2026-10-30', observed, fractions=(.02,))['scenarios'][0]['windows'][0]
    assert row['skipped_below_one_contract_at_risk_cap'] == 1
    assert row['skipped_insufficient_cash'] == 0
    with pytest.raises(ValueError, match='Debit fractions'):
        rolling_windows('2026-10-01', '2026-10-30', observed, fractions=(1.1,))
