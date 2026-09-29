from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import functools
import json

import pytest

import qte
from qte.experiments import Candidate, Evidence, ExperimentConfig, ResearchWindow, run_experiment


HOUR = 3_600_000_000_000


class RoundTrip(qte.Strategy):
    def __init__(self, quantity=10):
        super().__init__()
        self.quantity = quantity
        self.count = 0

    def on_bar(self, ctx, bar):
        if self.count == 0:
            ctx.submit_order(qte.market_order(bar.symbol, qte.OrderSide.BUY, self.quantity))
        elif self.count == 2:
            ctx.submit_order(qte.market_order(bar.symbol, qte.OrderSide.SELL, self.quantity))
        self.count += 1


class Cash(qte.Strategy):
    def on_bar(self, ctx, bar):
        pass


class Broken(qte.Strategy):
    def on_bar(self, ctx, bar):
        raise LookupError("deliberately broken candidate")


def data(start=0, *, rising=True, currency="USD", symbol="SPY", interval=HOUR):
    prices = [100.0, 100.0, 105.0, 110.0, 110.0] if rising else [100.0, 100.0, 95.0, 90.0, 90.0]
    return qte.Dataset.from_bars(
        [qte.Bar(symbol, start + i * interval, start + (i + 1) * interval,
                 price, price + 1, price - 1, price, 1000.0)
         for i, price in enumerate(prices)], interval_ns=interval,
        source_id="deterministic-unit-test-only", currency=currency)


def windows(*, rising=True, holdout_rising=True, kind="historical"):
    # Historical is a deliberate declaration fixture to test promotion gates,
    # not a claim that these fabricated unit-test bars are market observations.
    evidence = Evidence(kind, "unit-test-only", hashlib.sha256(b"unit-test-only").hexdigest(),
                        "close available at end_ns", "fabricated action-free test fixture")
    return tuple(ResearchWindow(role, data(i * 5 * HOUR, rising=holdout_rising if role == "test" else rising),
                                role, "up", evidence)
                 for i, role in enumerate(("train", "validation", "test")))


def config(**kwargs):
    return ExperimentConfig(min_closed_trades=1, min_validation_windows=1,
                            required_regimes=("up",), **kwargs)


def candidate(name="round-trip", quantity=10, **kwargs):
    return Candidate(name, {"quantity": quantity}, 8, lambda: RoundTrip(quantity), **kwargs)


def test_actual_engine_returns_costs_provenance_and_repeatability():
    first = run_experiment([candidate()], windows(), config())
    second = run_experiment([candidate()], windows(), config())
    assert first == second
    assert first.selected_candidate == "round-trip"
    assert first.holdout_status == "passed_research_screen_only"
    assert len(first.scenarios) == 6
    assert first.protocol_id.startswith("sha256:")
    assert first.candidates[0].factory_source_identity.startswith("sha256:")
    assert all(row.trade_count == 1 and row.fill_count == 2 for row in first.scenarios)
    assert all(row.normalized_config for row in first.scenarios)
    assert first.scenarios[1].total_return < first.scenarios[0].total_return
    assert first.scenarios[1].final_equity < first.scenarios[0].final_equity
    assert json.loads(json.dumps(asdict(first)))["selected_candidate"] == "round-trip"


def test_holdout_cannot_change_validation_selection_or_trigger_runner_up():
    slate = [candidate("large", 10), candidate("small", 5)]
    good = run_experiment(slate, windows(holdout_rising=True), config())
    bad = run_experiment(slate, windows(holdout_rising=False), config())
    assert good.selected_candidate == bad.selected_candidate == "large"
    assert good.assessments == bad.assessments
    assert bad.holdout_status == "failed"
    assert "stressed_return_gate_failed" in bad.holdout_reasons
    assert {row.candidate for row in bad.scenarios if row.role == "test"} == {"large"}
    assert good.protocol_id != bad.protocol_id  # The holdout dataset itself is recorded.


def test_all_losers_cash_benchmark_failures_and_exclusions_are_retained():
    slate = [candidate(), Candidate("cash", {}, 8, Cash, eligible=False),
             Candidate("broken", {}, 8, Broken)]
    report = run_experiment(slate, windows(rising=False), config())
    assert report.selected_candidate is None
    assert report.holdout_status == "not_run_no_selection"
    assert len(report.scenarios) == 12
    assert not any(row.role == "test" for row in report.scenarios)
    cash = [row for row in report.scenarios if row.candidate == "cash"]
    assert all(row.status == "ok" and row.trade_count == 0 and row.total_return == 0 for row in cash)
    failed = [row for row in report.scenarios if row.candidate == "broken"]
    assert all(row.status == "error" and "LookupError" in row.error for row in failed)
    assert "benchmark_only" in report.assessments[1].exclusions
    assert "scenario_failure" in report.assessments[2].exclusions


def test_synthetic_success_never_promotes_a_candidate():
    report = run_experiment([candidate()], windows(kind="synthetic"), config())
    assert all(row.total_return > 0 for row in report.scenarios)
    assert report.selected_candidate is None
    assert report.assessments[0].validation_score is not None
    assert "synthetic_not_promotion_evidence" in report.assessments[0].exclusions


def test_stress_failure_is_not_hidden_by_a_profitable_base_scenario():
    report = run_experiment([candidate()], windows(), config(stressed_costs=qte.ExecutionCosts(600, 10, 5)))
    base = [row for row in report.scenarios if row.cost_scenario == "base"]
    stress = [row for row in report.scenarios if row.cost_scenario == "stress"]
    assert all(row.total_return > 0 for row in base)
    assert all(row.total_return < 0 for row in stress)
    assert report.selected_candidate is None
    assert "stressed_return_gate_failed" in report.assessments[0].exclusions


def test_factory_runs_fresh_for_every_window_and_cost_and_reuse_is_rejected():
    created = []

    def fresh():
        result = RoundTrip()
        created.append(result)
        return result

    report = run_experiment([Candidate("fresh", {}, 8, fresh)], windows(), config())
    assert len(created) == 6
    assert len({id(strategy) for strategy in created}) == 6
    assert report.selected_candidate == "fresh"
    singleton = RoundTrip()
    report = run_experiment([Candidate("reused", {}, 8, lambda: singleton)], windows(), config())
    assert report.selected_candidate is None
    assert sum(row.status == "error" for row in report.scenarios) == 3
    assert all("reused a strategy instance" in row.error for row in report.scenarios[1:])


def test_non_strategy_factories_are_failed_trials_not_silent_skips():
    report = run_experiment([Candidate("wrong", {}, 8, lambda: object())], windows(), config())
    assert len(report.scenarios) == 4
    assert all(row.status == "error" and "qte.Strategy" in row.error for row in report.scenarios)
    assert report.selected_candidate is None


def test_ranking_ties_are_deterministic_by_candidate_name_not_input_order():
    first = run_experiment([candidate("zeta"), candidate("alpha")], windows(), config())
    second = run_experiment([candidate("alpha"), candidate("zeta")], windows(), config())
    assert first.selected_candidate == second.selected_candidate == "alpha"


def test_regime_coverage_trade_count_and_validation_count_are_explicit_gates():
    strict = ExperimentConfig(required_regimes=("up", "down"), min_validation_windows=2, min_closed_trades=5)
    result = run_experiment([candidate()], windows(), strict)
    assert set(result.assessments[0].exclusions) >= {
        "insufficient_regime_coverage", "insufficient_closed_trades", "insufficient_validation_windows"}
    assert result.selected_candidate is None


def test_worst_window_loss_floor_applies_even_when_mean_return_is_positive():
    train, validation, test = windows()
    strong = qte.Dataset.from_bars(
        [qte.Bar("SPY", (10 + i) * HOUR, (11 + i) * HOUR,
                 p, p + 1, p - 1, p, 1000)
         for i, p in enumerate((100, 100, 120, 140, 140))],
        interval_ns=HOUR, source_id="unit-test-only")
    parts = (train, replace(validation, data=data(5 * HOUR, rising=False)),
             replace(validation, name="strong_validation", data=strong),
             replace(test, data=data(15 * HOUR)))
    report = run_experiment([candidate(quantity=100)], parts,
                            config(worst_stressed_return=-0.005))
    assessment = report.assessments[0]
    assert assessment.validation_mean_stressed_return > 0
    assert assessment.validation_score[0] < -0.005
    assert "stressed_return_gate_failed" not in assessment.exclusions
    assert "worst_window_return_gate_failed" in assessment.exclusions
    assert report.selected_candidate is None


def test_drawdown_screen_uses_path_not_just_positive_terminal_return():
    train, validation, test = windows()
    reversal = qte.Dataset.from_bars(
        [qte.Bar("SPY", (5 + i) * HOUR, (6 + i) * HOUR,
                 p, p + 1, p - 1, p, 1000)
         for i, p in enumerate((100, 100, 90, 110, 110))],
        interval_ns=HOUR, source_id="unit-test-only")
    report = run_experiment([candidate()], (train, replace(validation, data=reversal), test),
                            config(max_drawdown=0.0005))
    rows = [row for row in report.scenarios if row.role == "validation"]
    assert all(row.total_return > 0 and row.maximum_drawdown > 0.0005 for row in rows)
    assert "drawdown_gate_failed" in report.assessments[0].exclusions
    assert report.selected_candidate is None


@pytest.mark.parametrize("change,match", [
    (lambda w: (w[0], replace(w[1], data=data(4 * HOUR)), w[2]), "non-overlapping"),
    (lambda w: (w[1], w[0], w[2]), "chronological"),
    (lambda w: (w[0], replace(w[1], role="test"), replace(w[2], role="validation")), "untouched"),
    (lambda w: (w[0], replace(w[1], data=data(5 * HOUR, currency="EUR")), w[2]), "currency"),
    (lambda w: (w[0], replace(w[1], data=data(5 * HOUR, symbol="QQQ")), w[2]), "universe"),
    (lambda w: (w[0], replace(w[1], data=data(5 * HOUR, interval=2 * HOUR)), w[2]), "interval"),
    (lambda w: w[:2], "declare train"),
    (lambda w: (w[0], replace(w[1], name="train"), w[2]), "unique"),
])
def test_split_contract_rejects_leakage_and_mismatched_data(change, match):
    with pytest.raises(ValueError, match=match):
        run_experiment([candidate()], change(windows()), config())


def test_parameters_are_deeply_frozen_and_have_strict_types():
    original = {"nested": {"periods": [5, 20]}}
    frozen = Candidate("frozen", original, 20, Cash)
    original["nested"]["periods"][0] = 100
    assert frozen.parameters["nested"]["periods"] == (5, 20)
    with pytest.raises(TypeError):
        frozen.parameters["nested"]["new"] = 1
    for parameters in ({1: "coerced-key"}, {"nan": float("nan")}, {"object": object()}):
        with pytest.raises(ValueError):
            Candidate("bad", parameters, 10, Cash)
    with pytest.raises(ValueError, match="history_capacity"):
        Candidate("bad", {}, True, Cash)


@pytest.mark.parametrize("kwargs", [
    {"initial_cash": True}, {"initial_cash": 0}, {"max_drawdown": float("nan")},
    {"max_drawdown": 1.1}, {"min_closed_trades": 0}, {"random_seed": True},
    {"random_seed": 2**64}, {"required_regimes": "low_vol"}, {"required_regimes": ()},
    {"required_regimes": ("up", "up")}, {"base_costs": qte.ExecutionCosts()},
    {"stressed_costs": qte.ExecutionCosts(1, 2, 1)}, {"max_gross_leverage": 2.0},
    {"initial_cash": 10**1000}, {"max_order_quantity": 2**63},
])
def test_experiment_config_rejects_invalid_inputs(kwargs):
    with pytest.raises(ValueError):
        ExperimentConfig(**kwargs)


def test_evidence_types_and_duplicate_slate_are_rejected():
    with pytest.raises(ValueError, match="source_sha256"):
        Evidence("historical", "unknown", "not-a-digest", "lagged", "action-free")
    with pytest.raises(ValueError, match="evidence kind"):
        Evidence("live", "unknown", "0" * 64, "lagged", "action-free")
    with pytest.raises(ValueError, match="candidate names"):
        run_experiment([candidate(), candidate()], windows(), config())


def test_factory_source_unavailable_is_explicitly_excluded():
    namespace = {"RoundTrip": RoundTrip}
    exec(compile("def opaque(): return RoundTrip()", "<opaque>", "exec"), namespace)
    report = run_experiment([Candidate("opaque", {}, 8, namespace["opaque"])], windows(), config())
    assert all(row.status == "ok" for row in report.scenarios)
    assert "unverified_factory_source" in report.assessments[0].exclusions
    assert report.selected_candidate is None


def test_partial_factory_source_fingerprints_underlying_implementation():
    report = run_experiment([Candidate("partial", {"quantity": 10}, 8,
                                     functools.partial(RoundTrip, quantity=10))], windows(), config())
    assert report.selected_candidate == "partial"
    assert report.candidates[0].factory_name.endswith("RoundTrip")
    assert report.candidates[0].factory_source_identity.startswith("sha256:")


def test_flat_risk_off_window_is_allowed_but_flat_all_is_not():
    class Conditional(qte.Strategy):
        def __init__(self):
            super().__init__()
            self.count = 0
            self.entered = False

        def on_bar(self, ctx, bar):
            if self.count == 0 and bar.close > 100:
                ctx.submit_order(qte.market_order(bar.symbol, qte.OrderSide.BUY, 10))
                self.entered = True
            elif self.count == 2 and self.entered:
                ctx.submit_order(qte.market_order(bar.symbol, qte.OrderSide.SELL, 10))
            self.count += 1

    def high_data(start):
        prices = (101.0, 101.0, 105.0, 110.0, 110.0)
        return qte.Dataset.from_bars(
            [qte.Bar("SPY", start + i * HOUR, start + (i + 1) * HOUR,
                     p, p + 1, p - 1, p, 1000.0) for i, p in enumerate(prices)],
            interval_ns=HOUR, source_id="unit-test-only")

    train, validation, test = windows()
    slate = [Candidate("conditional", {}, 8, Conditional)]
    parts = (train, replace(validation, data=high_data(5 * HOUR)),
             replace(validation, name="flat_validation", data=data(10 * HOUR)),
             replace(test, data=high_data(15 * HOUR)))
    report = run_experiment(slate, parts, config())
    assert report.selected_candidate == "conditional"
    assert report.assessments[0].validation_score[0] == 0
    assert report.assessments[0].validation_mean_stressed_return > 0
    cash_report = run_experiment([Candidate("cash", {}, 8, Cash)], parts, config())
    assert cash_report.selected_candidate is None
    assert "stressed_return_gate_failed" in cash_report.assessments[0].exclusions


def test_result_sink_receives_successful_raw_results_and_io_errors_abort():
    captured = []
    report = run_experiment([candidate()], windows(), config(),
                            on_result=lambda row, raw: captured.append((row, len(raw.fills))))
    assert len(captured) == len(report.scenarios) == 6
    assert all(count == 2 for _, count in captured)

    def broken_sink(row, raw):
        raise OSError("artifact output unavailable")

    with pytest.raises(OSError, match="artifact output unavailable"):
        run_experiment([candidate()], windows(), config(), on_result=broken_sink)


def test_obviously_synthetic_source_cannot_be_relabeled_to_promote():
    parts = windows()
    synthetic = qte.Dataset.from_bars(
        [qte.Bar("SPY", i * HOUR, (i + 1) * HOUR, 100, 101, 99, 100, 1000)
         for i in range(5)], interval_ns=HOUR, source_id="synthetic:demo")
    parts = (replace(parts[0], data=synthetic), *parts[1:])
    report = run_experiment([candidate()], parts, config())
    assert report.selected_candidate is None
    assert "synthetic_source_mislabeled_historical" in report.assessments[0].exclusions


def test_mid_run_package_source_change_aborts_without_a_completed_report(monkeypatch):
    import qte.experiments as module

    identities = iter(("sha256:before", "sha256:after"))
    monkeypatch.setattr(module, "source_identity", lambda: next(identities))
    artifacts = []
    with pytest.raises(RuntimeError, match="source files changed.*incomplete artifacts"):
        report = run_experiment([candidate()], windows(kind="synthetic"), config(),
                                on_result=lambda row, raw: artifacts.append(row.window))
        artifacts.append("completed-report")
    assert artifacts == ["train", "train", "validation", "validation"]


def test_mid_run_external_factory_source_change_also_aborts(monkeypatch):
    import qte.experiments as module

    original = module._candidate_record
    calls = 0

    def changed_record(value):
        nonlocal calls
        calls += 1
        record = original(value)
        return record if calls == 1 else replace(record, factory_source_identity="sha256:changed")

    monkeypatch.setattr(module, "_candidate_record", changed_record)
    with pytest.raises(RuntimeError, match="source files changed"):
        run_experiment([candidate()], windows(kind="synthetic"), config())
