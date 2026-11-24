import hashlib
import json
import math
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from qte import _core, search, truth_db
from qte.signal_engine import MINUTE_NS, SigmaReversal, run_session

NY = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")


# ---------------------------------------------------------------- kernel parity with the Python engine

def random_bars(seed, open_ns, anchor=100.0):
    from qte.signal_engine import MinuteBar
    rng, price, out = random.Random(seed), anchor, []
    for m in range(390):
        if rng.random() < 0.03:
            price += rng.choice((-1, 1)) * rng.uniform(1.5, 4.0)
        o = price
        price = max(1.0, price + rng.gauss(0, 0.35))
        hi, lo = max(o, price) + rng.uniform(0, 0.3), min(o, price) - rng.uniform(0, 0.3)
        start = open_ns + m * MINUTE_NS
        out.append(MinuteBar(start, start + MINUTE_NS, o, hi, lo, price))
    return out


@pytest.mark.parametrize("seed", range(40))
def test_cpp_sigma_reversal_matches_python_signal_engine(seed):
    from qte.signal_engine import SessionContext
    open_ns = int(datetime(2025, 4, 1, 9, 30, tzinfo=NY).timestamp()) * 10**9
    close_ns = open_ns + 390 * MINUTE_NS
    bars = random_bars(seed, open_ns)
    ctx = SessionContext("2025-04-01", open_ns, close_ns, 100.0, 4.0, 18.0, "range")
    cutoff = close_ns - 15 * MINUTE_NS  # Python filters holds past the close; the kernel flattens them
    expected = sorted(s.decided_ns for s in run_session(ctx, bars, reversal=SigmaReversal(bounds=(1,)))
                      if s.status == "signal" and s.decided_ns <= cutoff)
    kb = _core.SweepBars(MINUTE_NS, [b.start_ns for b in bars], [b.open for b in bars], [b.high for b in bars],
                         [b.low for b in bars], [b.close for b in bars], [0.0] * len(bars), [0] * len(bars))
    spec = search.to_kernel(search.normalize_spec(dict(
        family="sigma_reversal", sides="long_only", bound_sigma=1.0, hold_bars=6, max_trades_per_session=5)))
    result = _core.sweep_run(kb, [_core.SweepSession(100.0, 4.0, 18.0, 8)], search.costs_for("es"), spec)
    got = sorted(t.signal_ns for t in result.trades if t.signal_ns <= cutoff)
    assert got == expected


def test_parity_cases_are_not_vacuous():
    open_ns = int(datetime(2025, 4, 1, 9, 30, tzinfo=NY).timestamp()) * 10**9
    from qte.signal_engine import SessionContext
    ctx = SessionContext("2025-04-01", open_ns, open_ns + 390 * MINUTE_NS, 100.0, 4.0, 18.0, "range")
    total = sum(s.status == "signal" for seed in range(40)
                for s in run_session(ctx, random_bars(seed, open_ns), reversal=SigmaReversal(bounds=(1,))))
    assert total >= 5


# ---------------------------------------------------------------- spec handling

def test_spec_validation_and_grid():
    with pytest.raises(search.SearchError):
        search.normalize_spec({"family": "magic"})
    with pytest.raises(search.SearchError):
        search.normalize_spec({"family": "sigma_reversal", "bogus": 1})
    with pytest.raises(search.SearchError):
        search.normalize_spec({"family": "sigma_reversal", "band_window": 2.5})
    with pytest.raises(search.SearchError):
        search.normalize_spec({"family": "sigma_reversal", "regimes": ["bullish"]})
    with pytest.raises(ValueError):  # C++ range validation
        search.normalize_spec({"family": "opening_range_breakout", "range_bars": 0})
    specs = search.expand_grid({"family": "opening_range_breakout"}, {"range_bars": [3, 6], "stop_sigma": [0, 0.5]})
    assert len(specs) == 4 and len({search.spec_hash(s) for s in specs}) == 4
    with pytest.raises(search.SearchError):
        search.expand_grid({"family": "sigma_reversal"}, {"band_window": list(range(2, 300)),
                                                         "touch_lookback": list(range(1, 300))})


def test_deflated_sharpe_penalizes_more_trials():
    rng = random.Random(1)
    pnl = [rng.gauss(30, 200) for _ in range(250)]
    sharpe = sum(pnl) / len(pnl) / (sum((x - sum(pnl) / len(pnl)) ** 2 for x in pnl) / 249) ** .5 * math.sqrt(252)
    few = search.deflated_sharpe(sharpe, pnl, 2, 0.5)
    many = search.deflated_sharpe(sharpe, pnl, 5000, 0.5)
    assert 0 <= many < few <= 1


# ---------------------------------------------------------------- end-to-end harness on a synthetic truth DB

def _capture(path, symbol, candles, kind, n):
    response = dict(symbol=symbol, empty=False, candles=candles)
    digest = hashlib.sha256(json.dumps(response, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path.write_text(json.dumps(dict(provider="schwab", response=response, response_canonical_json_sha256=digest,
                                    response_received_utc_ns=4_000_000_000_000_000_000,
                                    request=dict(operation_id="priceHistory", query_parameters=dict(
                                        symbol=symbol, frequencyType=kind, frequency=n,
                                        needExtendedHoursData=False)))))
    return path


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(search, "SEARCH_DIR", tmp_path / "search")
    rng = random.Random(7)
    days, d = [], date(2022, 1, 3)
    while len(days) < 760:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    spx, vix, spy_daily, minutes, price = [], [], [], [], 400.0
    for day in days:
        stamp = int(datetime(day.year, day.month, day.day, tzinfo=CT).timestamp() * 1000)
        open_ms = int(datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY).timestamp() * 1000)
        first = price
        for k in range(78):
            o = price
            price = max(50.0, price + rng.gauss(0, 0.4))
            minutes.append(dict(datetime=open_ms + k * 300_000, open=o, high=max(o, price) + .1,
                                low=min(o, price) - .1, close=price, volume=1000 + rng.randint(0, 500)))
        hi = max(m["high"] for m in minutes[-78:])
        lo = min(m["low"] for m in minutes[-78:])
        spy_daily.append(dict(datetime=stamp, open=first, high=hi, low=lo, close=price, volume=1e6))
        spx.append(dict(datetime=stamp, open=first * 10, high=hi * 10, low=lo * 10, close=price * 10, volume=0))
        vix.append(dict(datetime=stamp, open=18, high=19, low=17, close=18, volume=0))
    caps = tmp_path / "caps"
    caps.mkdir()
    db = truth_db.connect(tmp_path / "truth.sqlite3")
    for name, symbol, rows, kind, n in [("spx", "$SPX", spx, "daily", 1), ("vix", "$VIX", vix, "daily", 1),
                                        ("spyd", "SPY", spy_daily, "daily", 1), ("spy5", "SPY", minutes, "minute", 5)]:
        truth_db.ingest_schwab(db, _capture(caps / f"{name}.json", symbol, rows, kind, n))
    yield db, tmp_path
    db.close()


def test_split_is_locked_and_holdout_never_reaches_agent_parts(harness):
    db, tmp = harness
    ws = search.Workspace(db, "SPY:5m:schwab")
    split = ws.split
    assert len(split.part("holdout")) == 252 and len(split.part("validation")) == 252
    assert split.part("dev")[-1] < split.part("validation")[0] < split.part("holdout")[0]
    used = set(ws.parts["dev"][2]) | set(ws.parts["validation"][2])
    assert not used & set(split.part("holdout"))
    path = search.split_path("SPY:5m:schwab")
    before = path.read_bytes()
    search.Workspace(db, "SPY:5m:schwab")  # reload does not rewrite the lock
    assert path.read_bytes() == before
    info = ws.describe()
    assert info["parts"]["holdout"] == {"sessions": 252, "locked": True}


def test_sweep_logs_every_trial_leaderboard_deflates_and_holdout_runs_once(harness):
    db, tmp = harness
    ws = search.Workspace(db, "SPY:5m:schwab")
    specs = search.expand_grid({"family": "opening_range_breakout", "sides": "both", "max_range_sigma": 0.0},
                               {"range_bars": [3, 6, 12], "stop_sigma": [0.0, 0.5], "hold_bars": [0, 12]})
    rows = search.evaluate(ws, specs, costs="spy_as_es", agent="tester")
    assert len(rows) == 12
    assert rows == sorted(rows, key=lambda r: (-min(r["dev"]["sharpe"], r["validation"]["sharpe"]), r["spec_hash"]))
    assert all(r["validation"]["trades"] > 0 for r in rows)
    again = search.evaluate(ws, specs[:2], costs="spy_as_es", agent="tester")
    assert [r["validation"] for r in again] == [
        next(x["validation"] for x in rows if x["spec_hash"] == r["spec_hash"]) for r in again]  # deterministic
    board = search.leaderboard("SPY:5m:schwab", costs="spy_as_es", min_trades=1, require_positive_dev=False)
    assert board["distinct_specs_tried"] == 12
    assert all(b["scored_on"] == "dev+validation" and b["scored_sessions"] > 252 for b in board["leaders"])
    probs = [b["deflated_sharpe_probability"] for b in board["leaders"]]
    assert all(p is None or 0 <= p <= 1 for p in probs)
    best = board["leaders"][0]
    result = search.run_holdout(ws, best["trial_id"])
    assert result["sessions"] == 252
    with pytest.raises(search.SearchError):
        search.run_holdout(ws, best["trial_id"])
    assert any(b["holdout"] for b in search.leaderboard("SPY:5m:schwab", costs="spy_as_es", min_trades=1,
                                                       require_positive_dev=False)["leaders"])


def test_cli_sweep_and_holdout_requires_confirmation(harness, capsys):
    db, tmp = harness
    from qte.cli import main
    args = ["search", "--truth-db", str(tmp / "truth.sqlite3")]
    assert main(args + ["sweep", "--dataset", "SPY:5m:schwab", "--costs", "spy_as_es", "--spec",
                        '{"family": "sigma_reversal", "sides": "both"}', "--grid", '{"bound_sigma": [0.5, 1.0]}']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["evaluated"] == 2
    trial = out["top"][0]["trial_id"]
    with pytest.raises(SystemExit):
        main(args + ["holdout", "--dataset", "SPY:5m:schwab", "--trial", str(trial)])


# ---------------------------------------------------------------- Alpaca ingest

def test_alpaca_ingest_uses_exchange_calendar_for_early_close(tmp_path):
    rows = []
    for day, last in (("2016-11-23", "15:55"), ("2016-11-25", "15:55")):
        t = datetime.fromisoformat(f"{day}T09:00").replace(tzinfo=NY)
        end = datetime.fromisoformat(f"{day}T{last}").replace(tzinfo=NY)
        while t <= end:
            rows.append(dict(t=t.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                             o=200, h=201, l=199, c=200, v=10, n=1, vw=200))
            t += timedelta(minutes=5)
    doc = {"_qte": {"schema": "alpaca-market-data-v2-stock-bars", "feed": "sip", "timeframe": "5Min",
                    "adjustment": "raw", "action_free": True}, "bars": {"SPY": rows}, "next_page_token": None}
    raw = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    folder = tmp_path / "abc"
    folder.mkdir()
    (folder / "bars.json").write_bytes(raw)
    (folder / "download.json").write_text(json.dumps(dict(
        version=1, request={"symbols": "SPY", "timeframe": "5Min"}, fetched_at_ns=4_000_000_000_000_000_000,
        sha256={"bars.json": hashlib.sha256(raw).hexdigest()})))
    db = truth_db.connect(tmp_path / "t.sqlite3")
    [result] = truth_db.ingest_paths(db, [tmp_path])
    assert result["provider"] == "alpaca" and result["rejected"] == 0
    counts = dict(db.execute("SELECT session_date, COUNT(*) FROM bars WHERE session='rth' GROUP BY session_date"))
    assert counts == {"2016-11-23": 78, "2016-11-25": 42}  # 13:00 early close the day after Thanksgiving
    (folder / "bars.json").write_bytes(raw.replace(b'"c":200', b'"c":200.5', 1))
    db2 = truth_db.connect(tmp_path / "t2.sqlite3")
    assert truth_db.ingest_paths(db2, [tmp_path])[0]["status"] == "error"  # checksum mismatch


def test_agent_tool_envelope_uses_fixed_context_and_has_no_holdout(harness):
    db, tmp = harness
    truth = tmp / "truth.sqlite3"
    ctx = {"dataset": "SPY:5m:schwab", "costs": "spy_as_es", "agent": "researcher"}
    described = search.agent_tool({"tool": "qte_describe", "input": {}, "context": ctx}, truth_path=truth)
    assert described["parts"]["holdout"]["locked"] is True
    out = search.agent_tool({"tool": "qte_sweep", "context": ctx, "output_dir": str(tmp / "out"),
                             "input": {"spec": {"family": "opening_range_breakout"},
                                       "grid": {"range_bars": [3, 6]}, "top": 99}}, truth_path=truth)
    assert out["evaluated"] == 2 and len(out["top"]) == 2
    assert (tmp / "out/all_results.json").exists()
    board = search.agent_tool({"tool": "qte_leaderboard", "input": {"min_trades": 1}, "context": ctx}, truth_path=truth)
    assert board["distinct_specs_tried"] == 2
    for bad in ("holdout", "qte_holdout"):
        with pytest.raises(search.SearchError):
            search.agent_tool({"tool": bad, "input": {}, "context": ctx}, truth_path=truth)
    with pytest.raises(search.SearchError):
        search.agent_tool({"tool": "qte_describe", "input": {"dataset": "SPY:5m:schwab"}, "context": {}},
                          truth_path=truth)


def test_diagnostics_by_year_regime_and_frictionless_override(harness):
    db, tmp = harness
    truth = tmp / "truth.sqlite3"
    ctx = {"dataset": "SPY:5m:schwab", "costs": "spy_as_es"}
    spec = {"family": "opening_range_breakout", "sides": "both", "max_range_sigma": 0.0}
    real = search.agent_tool({"tool": "qte_sweep", "context": ctx, "input": {"spec": spec}}, truth_path=truth)
    free = search.agent_tool({"tool": "qte_sweep", "context": ctx, "input": {"spec": spec,
                              "costs": "spy_as_es_frictionless"}}, truth_path=truth)
    r, f = real["top"][0], free["top"][0]
    assert sum(r["dev_net_by_year"].values()) == pytest.approx(r["dev"]["net_pnl"], abs=len(r["dev_net_by_year"]))
    assert r["dev_gross_before_costs"] == pytest.approx(f["dev"]["net_pnl"], abs=0.05)
    assert f["dev"]["net_pnl"] > r["dev"]["net_pnl"] and free["costs"] == "spy_as_es_frictionless"
    with pytest.raises(search.SearchError):  # agents cannot pick a cheaper "real" cost model
        search.agent_tool({"tool": "qte_sweep", "context": ctx, "input": {"spec": spec, "costs": "mes"}},
                          truth_path=truth)
    assert search.leaderboard("SPY:5m:schwab", costs="spy_as_es", min_trades=1)["distinct_specs_tried"] == 1


def test_leaderboard_excludes_specs_that_lose_on_dev_by_default(harness):
    db, tmp = harness
    ws = search.Workspace(db, "SPY:5m:schwab")
    specs = search.expand_grid({"family": "opening_range_breakout", "sides": "both", "max_range_sigma": 0.0},
                               {"range_bars": [2, 3, 6, 12], "hold_bars": [0, 6, 24]})
    rows = search.evaluate(ws, specs, costs="spy_as_es")
    strict = search.leaderboard("SPY:5m:schwab", costs="spy_as_es", min_trades=1)
    loose = search.leaderboard("SPY:5m:schwab", costs="spy_as_es", min_trades=1, require_positive_dev=False)
    assert all(b["dev"]["sharpe"] > 0 and b["validation"]["sharpe"] > 0 for b in strict["leaders"])
    losers = [r for r in rows if r["dev"]["sharpe"] <= 0 or r["validation"]["sharpe"] <= 0]
    assert len(loose["leaders"]) - len(strict["leaders"]) == len(losers)
    assert search.evaluate(ws, specs[:3], costs="spy_as_es", sort_by="dev")[0]["dev"]["sharpe"] == max(
        r["dev"]["sharpe"] for r in search.evaluate(ws, specs[:3], costs="spy_as_es"))
    with pytest.raises(search.SearchError):
        search.evaluate(ws, specs[:1], sort_by="luck")


def test_legacy_registry_gains_dev_pnl_column(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(search.REGISTRY_SCHEMA.replace(",\n  dev_session_pnl TEXT", ""))
    old.close()
    db = search.registry(path)
    assert "dev_session_pnl" in {r[1] for r in db.execute("PRAGMA table_info(trials)")}


def test_vix_gate_and_side_specific_regimes_in_vocabulary():
    spec = search.normalize_spec({"family": "opening_range_breakout", "short_regimes": ["trend_up", "range"],
                                  "long_regimes": ["range"], "min_prior_vix": 15, "max_prior_vix": 40})
    k = search.to_kernel(spec)
    assert k.allowed_regimes_short == (1 << search.REGIMES.index("trend_up")) | (1 << search.REGIMES.index("range"))
    assert k.allowed_regimes_long == 1 << search.REGIMES.index("range") and k.min_prior_vix == 15.0
    with pytest.raises(ValueError):
        search.normalize_spec({"family": "opening_range_breakout", "min_prior_vix": 30, "max_prior_vix": 20})
