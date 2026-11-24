"""Order preparation is offline and cannot bypass the paper/live boundary."""
import json
import socket

import pytest

from qte.brokers.staging import DraftJournal, OrderDraft, main, preview


ID = "qte-" + "a" * 32
OPTION_ID = "11111111-2222-4333-8444-555555555555"


def ticket(**changes):
    values = dict(broker="schwab", account_alias="test-only", asset="equity",
                  instrument="SPY", side="buy", position_effect="open", quantity=2,
                  limit_price="100.00", tick_size="0.01")
    return OrderDraft(**(values | changes))


@pytest.mark.parametrize("broker", ["schwab", "robinhood"])
def test_equity_entry_and_exit_preserve_economics(broker):
    for side, effect in (("buy", "open"), ("sell", "close")):
        p = preview(ticket(broker=broker, side=side, position_effect=effect), ID)
        assert p["live_submission_enabled"] is False
        assert p["blockers"]
        payload = p["provider_payload_draft"]
        if broker == "schwab":
            assert payload["price"] == "100"
            assert payload["orderLegCollection"][0] == {
                "instruction": side.upper(), "quantity": 2,
                "instrument": {"assetType": "EQUITY", "symbol": "SPY"}}
            assert payload["session"] == "NORMAL" and payload["duration"] == "DAY"
        else:
            assert payload["review_arguments"] == {
                "quantity": "2", "type": "limit", "time_in_force": "gfd",
                "market_hours": "regular_hours", "symbol": "SPY", "side": side,
                "limit_price": "100"}
            assert payload["placement_ref_id"] == ID
            assert "account_number" not in payload["review_arguments"]


@pytest.mark.parametrize("broker,instrument", [
    ("schwab", "SPXW  261127C07250000"), ("robinhood", OPTION_ID)])
@pytest.mark.parametrize("side,effect", [("buy", "open"), ("sell", "close"),
                                       ("sell", "open"), ("buy", "close")])
def test_option_open_close_never_inferred_from_side(broker, instrument, side, effect):
    draft = ticket(broker=broker, asset="option", instrument=instrument,
                   side=side, position_effect=effect, limit_price="1.55", tick_size="0.05")
    p = preview(draft, ID)["provider_payload_draft"]
    if broker == "schwab":
        assert p["orderLegCollection"][0]["instruction"] == f"{side}_TO_{effect}".upper()
        assert p["orderLegCollection"][0]["instrument"]["symbol"] == instrument
    else:
        assert p["review_tool"] == "review_option_order"
        assert p["review_arguments"]["legs"] == [{"option_id": instrument,
            "side": side, "position_effect": effect, "ratio_quantity": 1}]
        assert p["review_arguments"]["price"] == "1.55"
        assert "direction" not in p["review_arguments"]


@pytest.mark.parametrize("change", [
    {"quantity": True}, {"quantity": 1.5}, {"quantity": 0}, {"quantity": -1},
    {"limit_price": "NaN"}, {"limit_price": "Infinity"}, {"limit_price": 1.2},
    {"limit_price": "0"}, {"limit_price": "-1"}, {"limit_price": "1e4"},
    {"tick_size": "0"}, {"tick_size": "0.03", "limit_price": "1.00"},
    {"side": "sell"}, {"position_effect": "close"}, {"asset": "future"},
    {"broker": "unknown"}, {"account_alias": "123456789"}, {"instrument": "spy"},
    {"asset": "option", "instrument": "SPXW  261332C07250000"},
    {"asset": "option", "instrument": "SPXW  261127C00000000"},
    {"broker": "robinhood", "asset": "option", "instrument": "SPXW"},
])
def test_invalid_or_ambiguous_draft_rejected(change):
    with pytest.raises(ValueError):
        ticket(**change)


def test_identity_survives_restart_and_cannot_be_repurposed(tmp_path):
    path = tmp_path / "drafts.sqlite"
    with DraftJournal(path) as journal:
        original = journal.stage(ticket(), client_id=ID)
    with DraftJournal(path) as journal:
        assert journal.show(ID) == original
        assert journal.stage(ticket(limit_price="100.0"), client_id=ID) == original
        for change in ({"broker": "robinhood"}, {"account_alias": "another-account"},
                       {"quantity": 3}, {"limit_price": "101"}):
            with pytest.raises(ValueError, match="different draft"):
                journal.stage(ticket(**change), client_id=ID)
        assert journal.show(ID) == original
        assert journal.stage(ticket())["client_id"] != ID


def test_journal_tampering_is_detected(tmp_path):
    with DraftJournal(tmp_path / "drafts.sqlite") as journal:
        journal.stage(ticket(), client_id=ID)
        journal.db.execute("UPDATE order_drafts SET payload=?", (ticket(quantity=10).canonical_json(),))
        with pytest.raises(ValueError, match="integrity"):
            journal.show(ID)


def test_cli_stage_show_no_network_and_no_submit_command(tmp_path, monkeypatch, capsys):
    def deny_network(*args, **kwargs):
        pytest.fail("order staging attempted network access")
    monkeypatch.setattr(socket, "socket", deny_network)
    source = tmp_path / "ticket.json"
    source.write_text(ticket().canonical_json())
    args = ["--journal", str(tmp_path / "drafts.sqlite")]
    assert main(args + ["stage", str(source), "--client-id", ID]) == 0
    staged = json.loads(capsys.readouterr().out)
    assert main(args + ["show", ID]) == 0
    assert json.loads(capsys.readouterr().out) == staged
    with pytest.raises(SystemExit) as exc:
        main(args + ["submit", ID])
    assert exc.value.code == 2


def test_unknown_fields_rejected_without_creating_journal(tmp_path):
    source = tmp_path / "ticket.json"
    source.write_text(json.dumps(json.loads(ticket().canonical_json()) | {"type": "market"}))
    path = tmp_path / "drafts.sqlite"
    with pytest.raises(SystemExit):
        main(["--journal", str(path), "stage", str(source)])
    assert not path.exists()


def test_duplicate_json_fields_rejected(tmp_path):
    source = tmp_path / "ticket.json"
    source.write_text(ticket().canonical_json()[:-1] + ',"quantity":100}')
    path = tmp_path / "drafts.sqlite"
    with pytest.raises(SystemExit):
        main(["--journal", str(path), "stage", str(source)])
    assert not path.exists()
