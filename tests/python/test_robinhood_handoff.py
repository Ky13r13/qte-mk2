"""Fake host responses exercise the complete handoff without broker writes."""
from copy import deepcopy
import json
import os
import socket

import pytest

from qte.brokers.robinhood_handoff import RobinhoodHandoff, main
from qte.brokers.staging import OrderDraft


ID = "qte-" + "a" * 32


def observations(number="TEST4021", *, now=1000, manual=True, level="option_level_3"):
    return {"observed_at": now, "accounts": {"data": {"accounts": [{
        "account_number": number, "agentic_allowed": True, "state": "active",
        "deactivated": False, "permanently_deactivated": False, "option_level": level}]}},
        "approval": {"data": {"setting": {"account_number": number,
            "human_must_approve_trades": manual, "updated_at": "2026-01-01T00:00:00Z"}}}}


def draft(asset="equity"):
    return OrderDraft("robinhood", "hood-mcp", asset,
        "SPY" if asset == "equity" else "11111111-2222-4333-8444-555555555555",
        "buy", "open", 1, "100", "0.01")


def review(*, app=True, checks=None):
    return {"data": {"symbol": "SPY", "side": "buy", "type": "limit",
        "quantity": "1.0", "limit_price": "100.00", "order_checks": checks or {},
        "customer_approval_required": app,
        "customer_approval_required_reason": "account_setting" if app else None,
        "market_data_disclosure": "Exact provider disclosure."},
        "guide": "Exact provider workflow instructions."}


@pytest.fixture
def bridge(tmp_path):
    with RobinhoodHandoff(tmp_path / "drafts.sqlite", clock=lambda: 1000) as b:
        b.connect("hood-mcp", observations())
        b.stage(draft(), client_id=ID)
        yield b


def reviewed(bridge, response=None):
    request = bridge.review_request(ID, observations())
    bridge.record_review(ID, request["request_id"], review() if response is None else response)
    return request["request_id"]


def test_bound_equity_review_then_app_approval_handoff(bridge):
    request = bridge.review_request(ID, observations())
    assert request["tool"] == "mcp__hood__review_equity_order"
    assert request["arguments"] == {"account_number": "TEST4021", "symbol": "SPY",
        "side": "buy", "quantity": "1", "limit_price": "100", "type": "limit",
        "time_in_force": "gfd", "market_hours": "regular_hours"}
    recorded = bridge.record_review(ID, request["request_id"], review())
    assert recorded["review"] == review()
    placement = bridge.placement_request(ID, observations(), authorization="User test instruction")
    assert placement["tool"] == "mcp__hood__place_equity_order"
    assert placement["arguments"] == request["arguments"] | {"ref_id": ID}
    assert bridge.status(ID)["state"] == "submission_uncertain"
    receipt = {"content": [{"type": "text", "text": "Pending customer approval, not filled."}]}
    result = bridge.record_placement(ID, placement["request_id"], receipt)
    assert result["state"] == "broker_response_recorded"
    assert result["fill_status"] == "not_reconciled" and not result["accounting_applied"]
    assert bridge.record_placement(ID, placement["request_id"], receipt) == result
    with pytest.raises(ValueError, match="conflicting"):
        bridge.record_placement(ID, placement["request_id"], {"content": []})


def test_option_mapping_and_permissions(bridge):
    option = bridge.stage(draft("option"))
    request = bridge.review_request(option["client_id"], observations())
    assert request["tool"] == "mcp__hood__review_option_order"
    assert request["arguments"]["legs"][0]["position_effect"] == "open"
    bridge.record_review(option["client_id"], request["request_id"], review())
    result = bridge.placement_request(option["client_id"], observations(), authorization="test instruction")
    assert result["tool"] == "mcp__hood__place_option_order"
    other = bridge.stage(draft("option"))
    with pytest.raises(ValueError, match="options permission"):
        bridge.review_request(other["client_id"], observations(level="option_level_0"))


@pytest.mark.parametrize("app,checks", [(False, {}), (None, {}), (True, {"alert_type": "TEST_ALERT"})])
def test_confirmation_bound_to_exact_review(bridge, app, checks):
    request_id = reviewed(bridge, review(app=app, checks=checks))
    for confirmation in (None, "wrong-review-id"):
        with pytest.raises(ValueError, match="confirmation"):
            bridge.placement_request(ID, observations(), authorization="test instruction",
                                     confirmed_review_id=confirmation)
    result = bridge.placement_request(ID, observations(), authorization="test instruction",
                                     confirmed_review_id=request_id)
    assert result["arguments"]["ref_id"] == ID


def test_approval_setting_change_requires_new_review(bridge):
    reviewed(bridge)
    with pytest.raises(ValueError, match="setting changed"):
        bridge.placement_request(ID, observations(manual=False), authorization="test instruction")


def test_timeout_restart_and_error_never_blindly_retry(tmp_path):
    path = tmp_path / "drafts.sqlite"
    with RobinhoodHandoff(path, clock=lambda: 1000) as b:
        b.connect("hood-mcp", observations())
        b.stage(draft(), client_id=ID)
        request_id = reviewed(b)
        b.placement_request(ID, observations(), authorization="test instruction")
    with RobinhoodHandoff(path, clock=lambda: 1000) as b:
        assert b.status(ID)["state"] == "submission_uncertain"
        for action in (lambda: b.review_request(ID, observations()),
                       lambda: b.placement_request(ID, observations(), authorization="test instruction")):
            with pytest.raises(ValueError):
                action()
        b.record_placement(ID, request_id, {"isError": True, "content": []})
        assert b.status(ID)["state"] == "submission_uncertain"
        with pytest.raises(ValueError):
            b.placement_request(ID, observations(), authorization="test instruction")


def test_superseded_and_expired_reviews_are_rejected(bridge):
    first = bridge.review_request(ID, observations())
    second = bridge.review_request(ID, observations())
    with pytest.raises(ValueError, match="superseded"):
        bridge.record_review(ID, first["request_id"], review())
    bridge.record_review(ID, second["request_id"], review())
    bridge.clock = lambda: 1061
    with pytest.raises(ValueError, match="fresh"):
        bridge.placement_request(ID, observations(now=1061), authorization="test instruction")


def test_mismatched_review_leaves_awaiting_state(bridge):
    request = bridge.review_request(ID, observations())
    wrong = review()
    wrong["data"]["quantity"] = "100"
    with pytest.raises(ValueError, match="does not match"):
        bridge.record_review(ID, request["request_id"], wrong)
    assert bridge.status(ID)["state"] == "awaiting_review"


@pytest.mark.parametrize("change", ["wrong_account", "stale", "future", "disabled", "inactive",
                                    "unknown_approval", "wrong_approval_account", "ambiguous"])
def test_connection_fail_closed(bridge, change):
    obs = observations()
    account = obs["accounts"]["data"]["accounts"][0]
    if change == "wrong_account":
        obs = observations("TEST9999")
    elif change == "stale":
        obs["observed_at"] = 939
    elif change == "future":
        obs["observed_at"] = 1001
    elif change == "disabled":
        account["agentic_allowed"] = False
    elif change == "inactive":
        account["deactivated"] = True
    elif change == "unknown_approval":
        obs["approval"]["data"]["setting"]["human_must_approve_trades"] = None
    elif change == "wrong_approval_account":
        obs["approval"]["data"]["setting"]["account_number"] = "OTHER"
    else:
        obs["accounts"]["data"]["accounts"].append(deepcopy(account))
    with pytest.raises(ValueError):
        bridge.connect("hood-mcp", obs)
    with pytest.raises(ValueError):
        bridge.review_request(ID, obs)


def test_account_checkpoint_does_not_store_full_number(bridge):
    row = bridge.db.execute("SELECT * FROM hood_accounts").fetchone()
    assert "TEST4021" not in repr(row)
    assert bridge.connection("hood-mcp")["current_provider_state"] == "refresh_required"


def test_missing_authorization_does_not_reserve_submission(bridge):
    reviewed(bridge)
    with pytest.raises(ValueError, match="instruction"):
        bridge.placement_request(ID, observations(), authorization=" ")
    assert bridge.status(ID)["state"] == "reviewed"


def test_cli_connection_has_no_network(tmp_path, monkeypatch, capsys):
    import io
    import time
    def forbidden(*args, **kwargs):
        pytest.fail("bridge must not open network connections")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(observations(now=time.time()))))
    path = tmp_path / "journal.sqlite"
    assert main(["--journal", str(path), "connect", "hood-mcp"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["account_last4"] == "4021"
    assert output["autonomous_execution"] is False
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600
