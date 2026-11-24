from datetime import datetime
from statistics import median

import pytest

from qte import calm_condor_forward as forward, forward_paper, truth_db
from qte.brokers.schwab_auth import AuthError


def timestamp(clock):
    return int(datetime.fromisoformat(f"2026-11-24T{clock}-05:00").timestamp())*10**9


def test_expired_token_records_one_skip(tmp_path, monkeypatch):
    def expired(*args, **kwargs):
        raise AuthError("Access token expired or nearly expired; run refresh first.")
    monkeypatch.setattr(forward.schwab_session, "capture", expired)
    path = tmp_path/"observations"
    assert forward.observe(path, timestamp("09:54:59"))[0]["status"] == "outside_window"
    assert not path.exists()
    record, appended = forward.observe(path, timestamp("10:00:00"))
    assert appended and record["status"] == "blocked" and "expired" in record["reason"]
    assert forward.observe(path, timestamp("10:01:00")) == (record, False)
    assert forward.gate_status(path)["calm_sessions_observed"] == 0


def test_gate_excludes_non_calm_and_waits_for_twenty(tmp_path):
    path = tmp_path/"observations"
    scores = [.59]*9+[.61]*11
    forward_paper.append_session(path, forward.SCHEMA, dict(
        date="2026-10-01", status="non_calm", gate={"score": .2}))
    for i, score in enumerate(scores):
        forward_paper.append_session(path, forward.SCHEMA, dict(
            date=f"2026-11-{i+1:02}", status="observed", gate={"score": score}))
        r = forward.gate_status(path)
        assert r["passed"] == (i == 19)
    assert r["median_model_multiple"] == median(scores)


def test_paper_settle_uses_final_rth_bar_once(tmp_path, monkeypatch):
    observations, paper = tmp_path/"observations", tmp_path/forward_paper.LEDGER
    trade = dict(quoted_credit=2., modeled_credit=3., entry_fee_usd=5.,
                 legs=[dict(quantity=-1, right="P", strike=95), dict(quantity=1, right="P", strike=70),
                       dict(quantity=-1, right="C", strike=105), dict(quantity=1, right="C", strike=130)])
    forward_paper.append_session(observations, forward.SCHEMA, dict(date="2026-11-24", status="observed",
                                                                    trade=trade, asof_ns=timestamp("10:00:00")))
    bars = []
    monkeypatch.setattr(truth_db, "intraday", lambda *args, **kwargs: bars)
    assert forward.settle(observations, paper, None, timestamp("15:59:59")) == []
    assert forward.settle(observations, paper, None, timestamp("16:00:00"))[
        0]["status"] == "awaiting_final_spx_5m"
    assert not paper.exists()
    bars.append(dict(start_ns=timestamp("15:55:00"), close=150.))
    result, = forward.settle(observations, paper, None, timestamp("16:01:00"))
    assert result["payoff"] == 25. and result["paper_pnl_usd"] == -2305 and result["modeled_pnl_usd"] == -2205
    assert forward.settle(observations, paper, None, timestamp("16:02:00")) == [result]
    bars[0]["close"] = 100.
    with pytest.raises(forward_paper.LedgerMismatch, match="Settlement changed"):
        forward.settle(observations, paper, None, timestamp("16:03:00"))
