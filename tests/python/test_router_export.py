from __future__ import annotations

import json
from dataclasses import replace

import pytest

from qte.regimes import DealerObservation, FEATURES, MacroObservation, MacroRegimeFilter, RegimeConfig
from qte.router_export import validate_route_events, write_route_events
from qte.strategies.router import MacroRouterStrategy
from test_router import S, candidate, run


def _release(at=0, *, available=None):
    available = at if available is None else available
    return tuple(MacroObservation(feature, value, at, available, f"test:{feature}", f"v{at}")
                 for feature, value in zip(FEATURES, (3, 0, 0, 12)))


def _document(tmp_path, strategy):
    path = tmp_path / "route_events.json"
    write_route_events(path, strategy)
    return json.loads(path.read_text(encoding="utf-8"))


def test_writes_actual_route_events_with_asof_inputs_and_exact_integer_timestamps(tmp_path):
    router = MacroRouterStrategy("SPY", [candidate()], MacroRegimeFilter(_release()))
    run([100, 101, 102, 103, 104], router)
    value = _document(tmp_path, router)
    assert value["schema_version"] == 1 and value["artifact_kind"] == "router_events_v1"
    assert [event["timestamp_ns"] for event in value["events"]] == [event.timestamp_ns for event in router.route_events]
    event = value["events"][0]
    assert set(event) == {"timestamp_ns", "target_family", "active_family", "phase", "macro_regime",
                          "volatility_regime", "dealer_regime", "reasons", "macro_score",
                          "permitted_families", "macro_inputs", "dealer_input"}
    assert event["timestamp_ns"] == S and isinstance(event["timestamp_ns"], int)
    assert event["macro_inputs"][0]["available_ns"] <= event["timestamp_ns"]
    assert validate_route_events(value, start_ns=S, end_ns=5 * S) == value["events"]


def test_delayed_and_stale_asof_diagnostics_are_captured_from_the_router(tmp_path):
    delayed = tuple(replace(item, available_ns=4 * S) for item in _release())
    router = MacroRouterStrategy("SPY", [candidate()], MacroRegimeFilter(
        delayed, RegimeConfig(max_macro_age_ns=3 * S, max_volatility_age_ns=3 * S)))
    run([100, 101, 102, 103, 104, 105], router)
    events = _document(tmp_path, router)["events"]
    assert events[0]["phase"] == "cash" and events[0]["macro_inputs"] == []
    assert any("stale:growth_z" in event["reasons"] for event in events)


@pytest.mark.parametrize("mutation", [
    lambda event: event.__setitem__("timestamp_ns", True),
    lambda event: event["macro_inputs"][0].__setitem__("available_ns", event["timestamp_ns"] + 1),
    lambda event: event.__setitem__("phase", "active") or event.__setitem__("active_family", None),
    lambda event: event.__setitem__("permitted_families", ["trend", "trend"]),
])
def test_parser_rejects_bad_types_future_inputs_and_inconsistent_state(tmp_path, mutation):
    router = MacroRouterStrategy("SPY", [candidate()], MacroRegimeFilter(_release()))
    run([100, 101, 102, 103, 104], router)
    value = _document(tmp_path, router)
    mutation(value["events"][0])
    with pytest.raises(ValueError):
        validate_route_events(value, start_ns=S, end_ns=5 * S)


def test_parser_rejects_proxy_claiming_qualified_gamma(tmp_path):
    dealer = DealerObservation(-1, 0, 0, "dealer", "v1", "method", "SPX", "proxy")
    router = MacroRouterStrategy("SPY", [candidate()], MacroRegimeFilter(
        _release(), RegimeConfig(dealer_mode="optional"), dealer_observations=[dealer]))
    run([100, 101, 102, 103, 104], router)
    value = _document(tmp_path, router)
    event = value["events"][0]
    assert event["dealer_regime"] == "unknown"
    event["dealer_regime"] = "negative_gamma"
    with pytest.raises(ValueError, match="proxy"):
        validate_route_events(value, start_ns=S, end_ns=5 * S)


def test_writer_rejects_a_route_event_that_no_longer_matches_its_asof_decision(tmp_path):
    router = MacroRouterStrategy("SPY", [candidate()], MacroRegimeFilter(_release()))
    run([100, 101, 102, 103, 104], router)
    router._route_events[0] = replace(router.route_events[0], macro_regime="risk_off")
    with pytest.raises(ValueError, match="as-of"):
        write_route_events(tmp_path / "route_events.json", router)
