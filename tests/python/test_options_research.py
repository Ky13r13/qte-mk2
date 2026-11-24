from qte.options_research import run_options_research


def test_synthetic_options_research_is_deterministic_and_explicitly_bounded():
    first, second = run_options_research(), run_options_research()
    assert first == second
    assert first["evidence"] == "synthetic_known_mechanics_only"
    ids = [item["id"] for item in first["candidates"]]
    assert ids == ["no_confirmation_fixed", "confirmation_fixed", "confirmation_regime_sized",
                   "confirmation_no_chase_fixed", "perfect_data_oracle_infeasible"]
    oracle = next(item for item in first["candidates"] if item["oracle"])
    assert oracle["status"] == "infeasible_reference" and oracle["aggregate"]["net_pnl"] > 0
    assert all(trade["max_loss"] == trade["entry_debit"] + trade["fees"] and trade["quantity"] > 0
               and trade["entry_ns"] > trade["signal_asof_ns"] for trade in first["trades"])
    sized = [trade for trade in first["trades"] if trade["variant"] == "confirmation_regime_sized"]
    assert {trade["quantity"] for trade in sized} == {1, 2}
    assert all(trade["risk_deployed"] <= first["quote_policy"]["max_risk_dollars_per_trade"] for trade in sized)
    assert all("entry_pair_gamma" in trade["gamma_diagnostics"] for trade in first["trades"])


def test_regimes_are_predeclared_and_failure_does_not_create_a_fill():
    result = run_options_research()
    rows = {row["name"]: row for row in result["regimes"]}
    assert set(rows) == {"calm_chop", "wide_costs", "expansion", "entry_quote_failure"}
    assert all(row["variants"]["no_confirmation_fixed"]["trades"] == 0
               for name, row in rows.items() if name == "entry_quote_failure")
    assert rows["wide_costs"]["variants"]["confirmation_no_chase_fixed"]["trades"] == 0
    assert any(skip["reason"] == "atomic_entry_quote_unavailable" for skip in result["skipped"])
