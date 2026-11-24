"""Observed-quote availability and conservative coverage regressions."""
import csv
from datetime import date, datetime, time, timedelta
import json
from hashlib import sha256

import pytest

from qte.adapters.theta_quote_csv import QuoteSession, read_quotes, read_spot
from qte.spx_0dte_quotes import main, replay

SESSION = QuoteSession(date(2025, 11, 28), 4990, "PUT", time(13))
AT = datetime.combine(SESSION.day, time(10))
HEADER = "symbol expiration strike right timestamp bid ask bid_size ask_size bid_condition ask_condition".split()


def write_quotes(path, events):
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        for seconds, bid, ask in events:
            writer.writerow(("SPXW", SESSION.day, SESSION.strike, SESSION.right,
                             (AT + timedelta(seconds=seconds)).isoformat(), bid, ask, 1, 1, 50, 50))


def test_custom_capture_and_half_day_cli(tmp_path):
    spot = tmp_path / "index.csv"
    spot.write_text("timestamp,price\n2025-11-28T09:59:59,5000\n2025-11-28T10:00:00,5100\n")
    quotes = tmp_path / "chain.csv"
    write_quotes(quotes, [(-1, 1.9, 2), (1, 9, 10), (2, 1.9, 2), (3, 3, 3.1),
                          (4, 4, 4.1), (5, 2.8, 2.9)])
    output = tmp_path / "results"
    main(["--session", str(SESSION.day), "--strike", str(SESSION.strike), "--right", "PUT",
          "--close-et", "13:00", "--spot-csv", str(spot), "--quotes-csv", str(quotes), "--output", str(output)])
    report = json.loads((output / "report.json").read_text())
    assert report["right"] == "PUT"
    assert report["sources"] == {str(p): sha256(p.read_bytes()).hexdigest() for p in [spot, quotes]}
    assert all(row["decision"] < "13:00" for row in report["outcomes"])
    result = next(row for row in report["outcomes"] if row["decision"] == "10:00:00"
                  and row["target"] == .3 and row["latency_seconds"] == 1)
    assert result["prior_spot"] == 5000
    assert result["otm_fraction"] == pytest.approx(.002)
    assert (result["ask"], result["bid"], result["exit_reason"]) == (2, 2.8, "target")
    assert result["net_pnl"] == pytest.approx(77.4)
    assert "SPXW 2025-11-28 4990 put" in (output / "report.md").read_text()


def test_gap_and_out_of_order_update_cannot_create_retroactive_profit(tmp_path):
    path = tmp_path / "quoted.csv"
    write_quotes(path, [(-1, 1.9, 2), (2, 1.9, 2), (15, 4, 5), (4, 3.5, 3.6), (17, 4, 5)])
    quotes, counts = read_quotes(path, SESSION)
    assert counts == {"out_of_order_put": 1}
    prices = [(AT - timedelta(seconds=1), 5000)]
    row = next(r for r in replay(prices, quotes, SESSION) if r["decision"] == "10:00:00")
    assert (row["status"], row["exit_reason"], row["conservative_pnl"]) == (
        "unresolved_exit", "observation_gap", -201.30)
    assert "net_pnl" not in row


def test_capture_admission_errors(tmp_path):
    spot = tmp_path / "spot.csv"
    spot.write_text("timestamp,price\n2025-11-28T10:00:00,nan\n")
    with pytest.raises(ValueError, match="positive and finite"):
        read_spot(spot, SESSION)
    path = tmp_path / "quotes.csv"
    write_quotes(path, [(0, 1, 2)])
    with path.open("a") as stream:
        stream.write("SPXW,\nSPXW,\n")
    with pytest.raises(ValueError, match="Incomplete quote row inside"):
        read_quotes(path, SESSION)
    write_quotes(path, [(0, 10, 2)])
    with pytest.raises(ValueError, match="No usable PUT"):
        read_quotes(path, SESSION)
