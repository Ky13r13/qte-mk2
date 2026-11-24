import json
from importlib.metadata import version
from pathlib import Path

import pytest

from qte.adapters.session_bundle import load_session_bundle
from qte.market_sessions import NS, entry_window, import_xnys, load_schedule, main

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/session-bars.json"
EXPIRY = "2025-11-28"


def check(s, now_ns, product="SPXW_PM", **kw):
    return entry_window(s, product=product, expiry=EXPIRY, now_ns=now_ns,
                        hold_ns=240 * NS, buffer_ns=60 * NS, **kw)


def test_half_day_boundary_and_earlier_contract_or_broker_restriction():
    s = load_schedule(EXAMPLE)
    close = s.day(EXPIRY).close_ns
    last_entry = close - 300 * NS
    assert check(s, last_entry)["admitted"]
    assert check(s, last_entry)["remaining_ns"] == 300 * NS
    assert check(s, last_entry + 1)["reason"] == "insufficient_time"
    assert check(s, close)["reason"] == "at_or_after_cutoff"
    assert check(s, last_entry, contract_cutoff_ns=close - 1)["reason"] == "insufficient_time"
    assert check(s, last_entry, broker_cutoff_ns=close - NS,
                 contract_cutoff_ns=close + NS)["cutoff_ns"] == close - NS
    assert check(s, last_entry, broker_cutoff_ns=close + NS)["cutoff_ns"] == close


def test_desk_publishes_policy_for_fresh_clock_evaluation(tmp_path):
    from qte.market_sessions import schedule_from_dict

    args = ["check", "--desk", str(tmp_path), "--schedule", str(EXAMPLE), "--product", "SPXW_PM",
            "--expiry", EXPIRY, "--now-ns", "1764352500000000000", "--hold-seconds", "240", "--buffer-seconds", "60"]
    assert main(args) == 0
    config = json.loads((tmp_path / "session.json").read_bytes())
    schedule = schedule_from_dict(config.pop("schedule"))
    assert entry_window(schedule, **config, now_ns=1764352500000000000)["admitted"]
    assert entry_window(schedule, **config, now_ns=1764352500000000001)["reason"] == "insufficient_time"


def test_am_preceding_session_skips_holiday_and_never_enters_on_expiry():
    s = load_schedule(EXAMPLE)
    wed, _, fri = s.days
    am_window = check(s, wed.open_ns, "SPX_AM")
    assert am_window["admitted"]
    assert am_window["last_trading_date"] == wed.trading_date
    assert am_window["product_last_cash_close_ns"] == wed.close_ns
    assert check(s, fri.open_ns, "SPX_AM")["reason"] == "past_last_trading_date"
    assert check(s, fri.open_ns, "SPY_OPTION")["admitted"]
    assert check(s, fri.open_ns - 1)["reason"] == "before_open"
    assert check(s, fri.open_ns - 86400 * NS)["reason"] == "closed_session"
    with pytest.raises(ValueError, match="open date"):
        entry_window(s, product="SPXW_PM", expiry="2025-11-27", now_ns=wed.open_ns, hold_ns=0, buffer_ns=0)
    with pytest.raises(ValueError, match="outside schedule coverage"):
        entry_window(s, product="SPY_OPTION", expiry="2025-12-01", now_ns=wed.open_ns, hold_ns=0, buffer_ns=0)
    with pytest.raises(ValueError, match="preceding trading session"):
        entry_window(s, product="SPX_AM", expiry=wed.trading_date, now_ns=wed.open_ns, hold_ns=0, buffer_ns=0)


def test_actual_exchange_import_has_dst_half_day_holiday_and_stable_provenance(tmp_path):
    pytest.importorskip("exchange_calendars")
    out = tmp_path / "schedule.json"
    assert main(["export", "--start", "2025-10-31", "--end", EXPIRY, "--output", str(out)]) == 0
    snapshot = load_schedule(out)
    assert version("exchange-calendars") in snapshot.source_id
    assert snapshot.snapshot_sha256 == import_xnys("2025-10-31", EXPIRY).snapshot_sha256
    assert snapshot.day("2025-11-27").close_ns is None
    assert snapshot.day(EXPIRY).close_ns == 1764352800000000000
    friday, monday = (snapshot.day(d) for d in ("2025-10-31", "2025-11-03"))
    assert monday.open_ns - friday.open_ns == (3 * 24 + 1) * 3600 * NS
    assert import_xnys("2025-11-29", "2025-11-29").days[0].open_ns is None
    bad = json.loads(out.read_bytes())
    bad["days"][-1]["close_ns"] += 1
    out.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match="checksum"):
        load_schedule(out)


def test_export_with_captured_bars_enters_existing_dataset_loader(tmp_path):
    pytest.importorskip("exchange_calendars")
    bundle = json.loads(EXAMPLE.read_bytes())
    del bundle["schedule"]
    bars, out = tmp_path / "bars.json", tmp_path / "complete.json"
    bars.write_text(json.dumps(bundle))
    assert main(["export", "--start", "2025-11-26", "--end", EXPIRY, "--output", str(out),
                 "--bars-input", str(bars)]) == 0
    result = load_session_bundle(out.read_bytes())
    assert len(result.bars) == len(bundle["bars"])
    assert any("exchange_calendars/" in fact for fact in result.provenance.assumptions)
    with pytest.raises(SystemExit) as exc:
        main(["export", "--start", "2025-11-26", "--end", EXPIRY, "--output", str(out)])
    assert exc.value.code == 2
