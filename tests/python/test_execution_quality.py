from copy import deepcopy
from pathlib import Path

import pytest

from qte.execution_quality import measure
from qte.json_input import strict_json
from qte.market_sessions import load_schedule

ROOT = Path(__file__).resolve().parents[2] / "examples"
MS = 1_000_000
T = 1764343800 * 10**9
KEY = "SPXW  251128C06500000"


def probe(orders=None, **assumptions):
    config = strict_json((ROOT / "live-desk/probes.json").read_bytes())
    config["orders"] = orders or [dict(id="one", side="buy", quantity=2, decision_ns=T, contract=KEY)]
    config.update(assumptions)
    return config


def event(name, ms, epoch=1, **fields):
    return dict(event=name, received_utc_ns=T+ms*MS, epoch=epoch, **fields)


def quote(receipt, source, bid, ask, size=3):
    return event("quote", receipt, contract=KEY, source_quote_ms=T//MS+source,
                 bid=bid, ask=ask, bid_size=size, ask_size=size)


def replay(config, *events):
    schedule = load_schedule(ROOT / "session-bars.json")
    return measure(config, schedule, [event("subscribed", -1200), *events])


def test_visible_midpoint_arrival_side_commission_and_repeatability():
    config = probe()
    config["orders"].append(dict(config["orders"][0], id="sell", side="sell"))
    events = [quote(-200,-205,1,1.2), quote(-25,-30,1.4,1.6), quote(120,49,1.5,1.7),
              quote(130,50,1.6,1.8)]
    report = replay(config,*events)
    assert report == replay(deepcopy(config),*events)
    buy,sell = report["orders"]
    assert buy["decision_mid"] == sell["decision_mid"] == 1.1  # feed delay and tie rule
    assert buy["execution_quote"]["line"] == 5
    assert (buy["price"],sell["price"]) == (1.8,1.6)
    assert buy["shortfall_usd"] == 141.30 and sell["shortfall_usd"] == -98.70
    assert buy["source_to_receipt_ms"] == 80
    assert report["groups"][0]["shortfall_per_contract_usd"]["p95"] == 70.65


@pytest.mark.parametrize("events,expected", [
    ([quote(-100,-110,1,1.1),quote(100,60,1.2,1.3,1)], "insufficient_displayed_size"),
    ([quote(-100,-110,1,1.1),event("disconnect",10)], "subscription_changed"),
    ([quote(-100,-110,1,1.1),event("unavailable",-60,contract=KEY),quote(100,60,1.2,1.3)], "no_visible_quote"),
    ([quote(-100,-1900,1,1.1),quote(100,60,1.2,1.3)], "stale_decision_quote"),
    ([quote(-100,-110,1,1.1),quote(2100,2050,1.2,1.3)], "wait_expired"),
    ([quote(-100,-110,1,1.1),quote(-5,-10,1.2,1.3)], "outside_capture"),
])
def test_unfilled_outcomes(events,expected):
    [row] = replay(probe(),*events)["orders"]
    assert row["status"] == expected and "price" not in row


def test_recheck_hold_and_broker_cutoff_at_execution():
    cfg = probe(hold_ms=1000,buffer_ms=0)
    cfg["orders"][0]["broker_cutoff_ns"] = T+1050*MS
    [row] = replay(cfg,quote(-100,-110,1,1.1),quote(100,60,1.2,1.3))["orders"]
    assert row["session"]["admitted"] and row["status"] == "insufficient_time"


def test_malformed_capture_or_duplicate_intent_fails():
    cfg = probe()
    with pytest.raises(ValueError,match="ID"):
        replay(probe(cfg["orders"]*2))
    with pytest.raises(ValueError,match="regression"):
        replay(cfg, quote(10,0,1,1),quote(1,0,1,1))
