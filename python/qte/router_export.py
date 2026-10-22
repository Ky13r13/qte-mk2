"""Portable, descriptive route-event diagnostics for macro router runs.

The artifact records the router's already-produced decisions.  It is not an
execution log and it does not establish that the enclosed observations are
authentic.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path

from qte.regimes import DealerObservation, FAMILIES, FEATURES, MacroObservation, MacroRegimeFilter


_EVENT_KEYS = frozenset(("timestamp_ns", "target_family", "active_family", "phase",
                         "macro_regime", "volatility_regime", "dealer_regime", "reasons",
                         "macro_score", "permitted_families", "macro_inputs", "dealer_input"))
_MACRO_KEYS = frozenset(("feature", "value", "reference_ns", "available_ns", "source_id", "vintage_id"))
_DEALER_KEYS = frozenset(("signed_gamma", "reference_ns", "available_ns", "source_id", "vintage_id",
                          "methodology_id", "underlying", "classification", "unit"))
_MACRO_REGIMES = frozenset(("unknown", "risk_on", "neutral", "risk_off"))
_VOLATILITY_REGIMES = frozenset(("unknown", "low", "normal", "high", "extreme"))
_DEALER_REGIMES = frozenset(("ignored", "unknown", "negative_gamma", "zero_gamma", "positive_gamma"))
_PHASES = frozenset(("active", "retiring", "cash"))
_MAX_EVENTS = 100_000
_MAX_STRING = 512
_MAX_REASONS = 64


def _fail(message: str) -> None:
    raise ValueError(f"invalid router events artifact: {message}")


def _int64(value, name: str) -> int:
    if type(value) is not int or not -(2 ** 63) <= value < 2 ** 63:
        _fail(f"{name} must be a signed 64-bit integer")
    return value


def _finite(value, name: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{name} must be finite")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        _fail(f"{name} must be finite")
    return value


def _string(value, name: str) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > _MAX_STRING
            or any(0xD800 <= ord(char) <= 0xDFFF for char in value)):
        _fail(f"{name} must be a bounded nonempty string")
    return value


def _family(value, name: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or value not in FAMILIES:
        _fail(f"{name} must be a known strategy family")
    return value


def _macro(value, timestamp_ns: int, index: int) -> dict:
    if type(value) is not dict or set(value) != _MACRO_KEYS:
        _fail(f"macro_inputs[{index}] has unexpected fields")
    try:
        item = MacroObservation(**value)
    except (TypeError, ValueError) as error:
        _fail(f"macro_inputs[{index}] is invalid: {error}")
    if item.reference_ns > timestamp_ns or item.available_ns > timestamp_ns:
        _fail(f"macro_inputs[{index}] is not available at the event timestamp")
    for name in ("source_id", "vintage_id"):
        _string(value[name], f"macro_inputs[{index}].{name}")
    return asdict(item)


def _dealer(value, timestamp_ns: int) -> dict | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != _DEALER_KEYS:
        _fail("dealer_input has unexpected fields")
    try:
        item = DealerObservation(**value)
    except (TypeError, ValueError) as error:
        _fail(f"dealer_input is invalid: {error}")
    if item.reference_ns > timestamp_ns or item.available_ns > timestamp_ns:
        _fail("dealer_input is not available at the event timestamp")
    for name in ("source_id", "vintage_id", "methodology_id", "underlying", "unit"):
        _string(value[name], f"dealer_input.{name}")
    return asdict(item)


def _event(value, start_ns: int, end_ns: int, previous_ns: int | None, index: int) -> dict:
    if type(value) is not dict or set(value) != _EVENT_KEYS:
        _fail(f"events[{index}] has unexpected fields")
    timestamp_ns = _int64(value["timestamp_ns"], f"events[{index}].timestamp_ns")
    if not start_ns <= timestamp_ns <= end_ns:
        _fail(f"events[{index}].timestamp_ns is outside the requested range")
    if previous_ns is not None and timestamp_ns <= previous_ns:
        _fail("event timestamps must be strictly increasing")
    target = _family(value["target_family"], f"events[{index}].target_family", nullable=True)
    active = _family(value["active_family"], f"events[{index}].active_family", nullable=True)
    phase = value["phase"]
    if type(phase) is not str or phase not in _PHASES:
        _fail(f"events[{index}].phase is unknown")
    if phase == "active" and (active is None or target != active):
        _fail("active phase must have equal active and target families")
    if phase == "cash" and (active is not None or target is not None):
        _fail("cash phase must not have an active or target family")
    if phase == "retiring" and active is None:
        _fail("retiring phase must have an active family")
    for name, labels in (("macro_regime", _MACRO_REGIMES),
                         ("volatility_regime", _VOLATILITY_REGIMES),
                         ("dealer_regime", _DEALER_REGIMES)):
        if type(value[name]) is not str or value[name] not in labels:
            _fail(f"events[{index}].{name} is unknown")
    reasons = value["reasons"]
    if type(reasons) is not list or len(reasons) > _MAX_REASONS:
        _fail(f"events[{index}].reasons must be a bounded list")
    reasons = [_string(item, f"events[{index}].reasons") for item in reasons]
    score = value["macro_score"]
    if score is not None:
        _finite(score, f"events[{index}].macro_score")
    permitted = value["permitted_families"]
    if type(permitted) is not list or len(permitted) > len(FAMILIES):
        _fail(f"events[{index}].permitted_families must be a bounded list")
    permitted = [_family(item, f"events[{index}].permitted_families") for item in permitted]
    if len(set(permitted)) != len(permitted):
        _fail(f"events[{index}].permitted_families must not repeat families")
    if permitted != [family for family in FAMILIES if family in permitted]:
        _fail(f"events[{index}].permitted_families must use canonical family order")
    macro_inputs = value["macro_inputs"]
    if type(macro_inputs) is not list or len(macro_inputs) > len(FEATURES):
        _fail(f"events[{index}].macro_inputs must be a bounded list")
    macro_inputs = [_macro(item, timestamp_ns, item_index) for item_index, item in enumerate(macro_inputs)]
    features = [item["feature"] for item in macro_inputs]
    if features != [feature for feature in FEATURES if feature in features]:
        _fail(f"events[{index}].macro_inputs must have distinct canonical features")
    dealer_input = _dealer(value["dealer_input"], timestamp_ns)
    if dealer_input is not None and dealer_input["classification"] == "proxy":
        valid_proxy_reason = ("dealer:proxy_not_positioning" in reasons or "dealer:stale" in reasons)
        if value["dealer_regime"] != "unknown" or not valid_proxy_reason:
            _fail("proxy dealer input must not qualify as gamma positioning")
    return {"timestamp_ns": timestamp_ns, "target_family": target, "active_family": active,
            "phase": phase, "macro_regime": value["macro_regime"],
            "volatility_regime": value["volatility_regime"], "dealer_regime": value["dealer_regime"],
            "reasons": reasons, "macro_score": score, "permitted_families": permitted,
            "macro_inputs": macro_inputs, "dealer_input": dealer_input}


def validate_route_events(value, *, start_ns: int, end_ns: int) -> list[dict]:
    """Strictly validate a parsed ``router_events_v1`` document.

    Validation establishes structural and point-in-time consistency only; it
    cannot authenticate the source observations or infer any execution result.
    """
    start_ns = _int64(start_ns, "start_ns")
    end_ns = _int64(end_ns, "end_ns")
    if start_ns > end_ns:
        _fail("start_ns must not follow end_ns")
    if type(value) is not dict or set(value) != {"schema_version", "artifact_kind", "events"}:
        _fail("envelope has unexpected fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _fail("schema_version must be 1")
    if value["artifact_kind"] != "router_events_v1":
        _fail("artifact_kind must be router_events_v1")
    events = value["events"]
    if type(events) is not list or len(events) > _MAX_EVENTS:
        _fail("events must be a bounded list")
    owned: list[dict] = []
    previous_ns = None
    for index, item in enumerate(events):
        checked = _event(item, start_ns, end_ns, previous_ns, index)
        owned.append(checked)
        previous_ns = checked["timestamp_ns"]
    return owned


def write_route_events(path: Path, strategy) -> None:
    """Write one descriptive event per actual router route event.

    ``explain`` is called once for every recorded event timestamp so captured
    provenance is the same as-of decision used to describe that event.
    """
    regime_filter = getattr(strategy, "regime_filter", None)
    if not isinstance(regime_filter, MacroRegimeFilter):
        raise TypeError("strategy must expose a MacroRegimeFilter regime_filter")
    route_events = tuple(getattr(strategy, "route_events", ()))
    records = []
    for event in route_events:
        timestamp_ns = _int64(getattr(event, "timestamp_ns", None), "route event timestamp_ns")
        decision = regime_filter.explain(timestamp_ns)
        event_fields = ("macro_regime", "volatility_regime", "dealer_regime", "reasons")
        if any(getattr(event, name, None) != getattr(decision, name) for name in event_fields):
            raise ValueError("route event does not match its as-of regime decision")
        records.append({"timestamp_ns": timestamp_ns,
                        "target_family": getattr(event, "target_family", None),
                        "active_family": getattr(event, "active_family", None),
                        "phase": getattr(event, "phase", None),
                        "macro_regime": decision.macro_regime,
                        "volatility_regime": decision.volatility_regime,
                        "dealer_regime": decision.dealer_regime,
                        "reasons": list(decision.reasons), "macro_score": decision.macro_score,
                        "permitted_families": list(decision.permitted_families),
                        "macro_inputs": [asdict(item) for item in decision.macro_inputs],
                        "dealer_input": None if decision.dealer_input is None else asdict(decision.dealer_input)})
    document = {"schema_version": 1, "artifact_kind": "router_events_v1", "events": records}
    # Check the generated content too, without a second filter call.
    if records:
        validate_route_events(document, start_ns=records[0]["timestamp_ns"], end_ns=records[-1]["timestamp_ns"])
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(document, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
