from qte.weekly_reversal import (CANDIDATE_DEFINITIONS, FIVE_MIN_NS, YEAR_NS,
                                 replay_weekly_reversal)


DAY = 86_400_000_000_000


def _payload():
    bars = [
        {"timestamp_ns": 0, "available_ns": 1, "close": 100.0},
        {"timestamp_ns": FIVE_MIN_NS, "available_ns": FIVE_MIN_NS + 1, "close": 102.0},
        {"timestamp_ns": 2 * FIVE_MIN_NS, "available_ns": 2 * FIVE_MIN_NS + 1, "close": 100.5},
        {"timestamp_ns": 3 * FIVE_MIN_NS, "available_ns": 3 * FIVE_MIN_NS + 1, "close": 100.4},
    ]
    quotes = []
    for contract in ("C", "P"):
        quotes.extend((
            {"contract_id": contract, "timestamp_ns": 2 * FIVE_MIN_NS + 2, "available_ns": 2 * FIVE_MIN_NS + 3, "bid": 1.0, "ask": 1.1},
            {"contract_id": contract, "timestamp_ns": 3 * FIVE_MIN_NS + 5, "available_ns": 3 * FIVE_MIN_NS + 6, "bid": 1.3, "ask": 1.4},
        ))
    return {"sessions": [{"id": "s", "open_ns": 0, "end_ns": 4 * FIVE_MIN_NS,
        "opening_daily_iv": {"timestamp_ns": 0, "available_ns": 0, "forward": 100.0,
                             "iv": 1.6 / (100 * (4 * FIVE_MIN_NS / YEAR_NS) ** .5),
                             "expiry_ns": 4 * FIVE_MIN_NS},
        "weekly": {"call_contract_id": "C", "put_contract_id": "P", "expiry_ns": 6 * DAY}, "bars": bars, "option_quotes": quotes}],
        "news_flags": [], "costs": {"base": {"fee_per_contract": 1.0, "spread_bps": 0.0}, "stress": {"fee_per_contract": 2.0, "spread_bps": 100.0}},
        "cost_scenario": "base", "max_debit_dollars": 650.0, "multiplier": 100}


def test_causal_reentry_uses_subsequent_quote_and_reports_cadence():
    result = replay_weekly_reversal(_payload())
    assert [item["id"] for item in result["candidate_definitions"]] == [item["id"] for item in CANDIDATE_DEFINITIONS]
    candidate = next(item for item in result["candidates"] if item["id"] == "confirmed_fade_1sigma")
    trade = candidate["trades"][0]
    assert trade["entry_ns"] > trade["decision_available_ns"]
    assert trade["exit_ns"] >= trade["entry_ns"] + FIVE_MIN_NS
    assert result["cadence_summary"]["five_minute_decision_bars"]["mean"] == 4
    assert result["short_leg_support"]["status"] == "excluded"


def test_debit_cap_and_weekly_expiry_are_enforced():
    payload = _payload(); payload["max_debit_dollars"] = 10.0
    result = replay_weekly_reversal(payload)
    candidate = next(item for item in result["candidates"] if item["id"] == "confirmed_fade_1sigma")
    assert any(item["reason"] == "debit_cap" for item in candidate["skipped"])
    payload = _payload(); payload["sessions"][0]["weekly"]["expiry_ns"] = 2 * DAY
    try: replay_weekly_reversal(payload)
    except ValueError as error: assert "5-9" in str(error)
    else: raise AssertionError("non-weekly expiry accepted")


def _candidate(result, name="confirmed_fade_1sigma"):
    return next(item for item in result["candidates"] if item["id"] == name)


def test_news_veto_is_point_in_time_and_resets_on_clear():
    data = _payload()
    gate = "regime_gated_confirmed_1sigma"
    event = {"timestamp_ns": FIVE_MIN_NS, "available_ns": 2 * FIVE_MIN_NS + 4, "active": True}
    data["news_flags"] = [event]
    assert len(_candidate(replay_weekly_reversal(data), gate)["trades"]) == 1
    event["available_ns"] = FIVE_MIN_NS + 1
    blocked = _candidate(replay_weekly_reversal(data), gate)
    assert not blocked["trades"] and blocked["skipped"][0]["reason"] == "active_news"
    data["news_flags"].append({"timestamp_ns": FIVE_MIN_NS + 2, "available_ns": FIVE_MIN_NS + 2, "active": False})
    assert len(_candidate(replay_weekly_reversal(data), gate)["trades"]) == 1


def test_news_observed_after_signal_before_quote_cancels_entry():
    data = _payload()
    data["news_flags"] = [{"timestamp_ns": FIVE_MIN_NS, "available_ns": 2 * FIVE_MIN_NS + 2, "active": True}]
    row = _candidate(replay_weekly_reversal(data), "regime_gated_confirmed_1sigma")
    assert not row["trades"] and row["skipped"][0]["reason"] == "news_before_fill"


def test_round_trip_cap_and_unresolved_exit_are_accounted():
    data = _payload()
    data["max_debit_dollars"] = 111.50
    assert not _candidate(replay_weekly_reversal(data))["trades"]
    data["max_debit_dollars"] = 200.0
    for quote in data["sessions"][0]["option_quotes"]:
        if quote["timestamp_ns"] > 3 * FIVE_MIN_NS:
            quote["bid"] = 0.0
    row = _candidate(replay_weekly_reversal(data))
    assert len(row["unresolved"]) == 1 and not row["trades"]
    assert row["aggregate"]["net_pnl"] == -row["unresolved"][0]["risk_deployed"]


def test_delayed_previous_bar_cannot_supply_a_reentry():
    data = _payload()
    data["sessions"][0]["bars"][1]["available_ns"] = 2 * FIVE_MIN_NS + 2
    assert not _candidate(replay_weekly_reversal(data))["trades"]
