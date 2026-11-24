import hashlib
import json
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from qte import truth_db as t

CT = ZoneInfo("America/Chicago")
NY = ZoneInfo("America/New_York")


def capture(path, symbol, candles, *, kind="daily", n=1, received_ns=None, returned=None):
    response = dict(symbol=returned or symbol, empty=False, candles=candles)
    digest = hashlib.sha256(json.dumps(response, allow_nan=False, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(
        provider="schwab", schema_version=1, response=response, response_canonical_json_sha256=digest,
        response_received_utc_ns=received_ns if received_ns is not None else 2_000_000_000_000_000_000,
        request=dict(operation_id="priceHistory", path_parameters={}, query_parameters=dict(
            symbol=symbol, frequencyType=kind, frequency=n, needExtendedHoursData=False)))))
    return path


def day_ms(d):
    return int(datetime.fromisoformat(d).replace(tzinfo=CT).timestamp() * 1000)


def candle(d, o, h, l, c, v=1000):
    return dict(datetime=day_ms(d), open=o, high=h, low=l, close=c, volume=v)


@pytest.fixture
def db(tmp_path):
    conn = t.connect(tmp_path / "truth.sqlite3")
    yield conn
    conn.close()


def test_ingest_maps_symbols_dates_and_is_idempotent(db, tmp_path):
    path = capture(tmp_path / "c/spx.json", "$SPX", [candle("2026-11-20", 10, 11, 9, 10.5)])
    first = t.ingest_schwab(db, path)
    assert first["instrument"] == "SPX" and first["inserted"] == 1
    assert t.ingest_schwab(db, path)["status"] == "already_ingested"
    assert t.daily(db, "SPX") == {"2026-11-20": dict(date="2026-11-20", open=10, high=11, low=9, close=10.5,
                                                     volume=1000, quality="ok")}


def test_rejects_bad_ohlc_and_marks_close_only(db, tmp_path):
    path = capture(tmp_path / "c/vix.json", "$VIX", [
        candle("2026-11-18", 20, 19, 18, 20), candle("2026-11-19", 20, 20, 20, 20), candle("2026-11-20", 20, 21, 19, 20)])
    assert t.ingest_schwab(db, path)["rejected"] == 1
    assert db.execute("SELECT reason FROM rejected").fetchall() == [("inconsistent_ohlc",)]
    assert {d: r["quality"] for d, r in t.daily(db, "VIX").items()} == {"2026-11-19": "close_only", "2026-11-20": "ok"}


def test_provisional_bar_is_finalized_and_final_bar_is_immutable(db, tmp_path):
    during = int(datetime(2026, 11, 20, 12, tzinfo=NY).timestamp()) * 10**9
    after = int(datetime(2026, 11, 20, 18, tzinfo=NY).timestamp()) * 10**9
    capture(tmp_path / "a.json", "$SPX", [candle("2026-11-20", 10, 11, 9, 10)], received_ns=during)
    capture(tmp_path / "b.json", "$SPX", [candle("2026-11-20", 10, 12, 9, 11.5)], received_ns=after)
    capture(tmp_path / "c.json", "$SPX", [candle("2026-11-20", 10, 12, 9, 11.4)], received_ns=after + 1)
    t.ingest_schwab(db, tmp_path / "a.json")
    assert t.daily(db, "SPX") == {}  # provisional bars are excluded from research reads
    t.ingest_schwab(db, tmp_path / "b.json")
    assert t.daily(db, "SPX")["2026-11-20"]["close"] == 11.5
    t.ingest_schwab(db, tmp_path / "c.json")
    assert t.daily(db, "SPX")["2026-11-20"]["close"] == 11.5  # first final value kept
    assert [k for (k,) in db.execute("SELECT kind FROM revisions ORDER BY rowid")] == ["finalized", "conflict"]


def test_ambiguous_duplicate_timestamps_are_rejected_identical_collapse(db, tmp_path):
    start = int(datetime(2026, 11, 23, 9, 30, tzinfo=NY).timestamp() * 1000)
    rows = [dict(datetime=start, open=1, high=2, low=1, close=2, volume=5),
            dict(datetime=start, open=1, high=2, low=1, close=2, volume=6),
            dict(datetime=start + 60_000, open=1, high=2, low=1, close=2, volume=5),
            dict(datetime=start + 60_000, open=1, high=2, low=1, close=2, volume=5)]
    result = t.ingest_schwab(db, capture(tmp_path / "m.json", "$SPX", rows, kind="minute", n=1))
    assert (result["inserted"], result["rejected"]) == (1, 2)
    [bar] = t.intraday(db, "SPX", "1m")
    assert bar["start_ns"] == (start + 60_000) * 10**6


def test_futures_root_accepts_contract_symbol_but_equities_do_not(db, tmp_path):
    es = capture(tmp_path / "es.json", "/ES", [candle("2026-11-20", 10, 11, 9, 10)], returned="/ESZ26")
    assert t.ingest_schwab(db, es)["instrument"] == "ES"
    assert json.loads(db.execute("SELECT request_json FROM captures").fetchone()[0])["returned_symbol"] == "/ESZ26"
    bad = capture(tmp_path / "spy.json", "SPY", [candle("2026-11-20", 10, 11, 9, 10)], returned="QQQ")
    with pytest.raises(t.TruthError):
        t.ingest_schwab(db, bad)


def test_directory_ingest_skips_test_fixture_and_demo_folders(db, tmp_path):
    capture(tmp_path / "root/real/spx.json", "$SPX", [candle("2026-11-20", 10, 11, 9, 10)])
    capture(tmp_path / "root/test-capture/spx.json", "$SPX", [candle("2026-11-19", 99, 99, 99, 99)])
    capture(tmp_path / "root/live-demo/spx.json", "$SPX", [candle("2026-11-18", 99, 99, 99, 99)])
    (tmp_path / "root/real/other.json").write_text("{\"not\": \"a capture\"}")
    results = t.ingest_paths(db, [tmp_path / "root"])
    assert [r["status"] for r in results] == ["ingested"]
    assert list(t.daily(db, "SPX")) == ["2026-11-20"]


def test_checksum_mismatch_is_an_error(db, tmp_path):
    path = capture(tmp_path / "x.json", "$SPX", [candle("2026-11-20", 10, 11, 9, 10)])
    doc = json.loads(path.read_text())
    doc["response"]["candles"][0]["close"] = 10.5
    path.write_text(json.dumps(doc))
    assert t.ingest_paths(db, [path])[0]["status"] == "error"


def test_regime_outcomes_measure_moves_in_expected_move_units(db, tmp_path):
    start = datetime(2020, 1, 1)
    days, spx, vix, price = [], [], [], 100.0
    d = start
    while len(days) < 330:
        if d.weekday() < 5:
            days.append(d.date().isoformat())
        d += timedelta(days=1)
    for i, day in enumerate(days):
        price *= 1.001
        spx.append(candle(day, price, price * 1.004, price * 0.996, price))
        vix.append(candle(day, 16, 16.5, 15.5, 16))
    # Final session: a clean trend-up day closing 2 expected moves above the prior close.
    prior = spx[-2]["close"]
    move = prior * 16 / 100 / math.sqrt(252)
    spx[-1] = candle(days[-1], prior, prior + 2 * move, prior - 0.01, prior + 2 * move)
    t.ingest_schwab(db, capture(tmp_path / "spx.json", "$SPX", spx))
    t.ingest_schwab(db, capture(tmp_path / "vix.json", "$VIX", vix))
    report = t.regime_outcomes(db)
    last = report["sessions"][-1]
    assert last["date"] == days[-1]
    assert last["close_sigma"] == pytest.approx(2.0)
    assert last["trend_up"] and not last["trend_down"] and not last["reversal_from_low"]
    assert report["all"]["sessions"] == len(report["sessions"]) == 330 - 300
    assert sum(r["sessions"] for r in report["regimes"].values()) == report["all"]["sessions"]
    assert "| ALL |" in t.render_regimes(report)


def test_qte_cli_dispatches_truth(tmp_path, capsys):
    from qte.cli import main
    capture(tmp_path / "caps/spx.json", "$SPX", [candle("2026-11-20", 10, 11, 9, 10)])
    db_path = tmp_path / "t.sqlite3"
    assert main(["truth", "--db", str(db_path), "ingest", str(tmp_path / "caps")]) == 0
    assert main(["truth", "--db", str(db_path), "coverage"]) == 0
    assert '"instrument": "SPX"' in capsys.readouterr().out


def test_daily_restamp_at_eastern_midnight_does_not_create_a_phantom_session(db, tmp_path):
    """Regression (2026-11-23): Schwab re-stamped the newest daily candle 05:00 UTC instead of 06:00 UTC."""
    central = int(datetime(2026, 11, 23, tzinfo=CT).timestamp() * 1000)   # 06:00 UTC
    eastern = int(datetime(2026, 11, 23, tzinfo=NY).timestamp() * 1000)   # 05:00 UTC
    row = dict(open=10, high=11, low=9, close=10.5, volume=1)
    capture(tmp_path / "a.json", "$VIX", [dict(row, datetime=central)])
    capture(tmp_path / "b.json", "$VIX", [dict(row, datetime=eastern)])
    t.ingest_schwab(db, tmp_path / "a.json")
    t.ingest_schwab(db, tmp_path / "b.json")
    assert list(t.daily(db, "VIX")) == ["2026-11-23"]
    assert db.execute("SELECT COUNT(*) FROM revisions").fetchone()[0] == 0


def test_monthly_and_weekly_candles_stay_provisional_until_their_period_ends(db, tmp_path):
    mid_month = int(datetime(2026, 11, 20, 18, tzinfo=NY).timestamp()) * 10**9
    month = [candle("2026-10-01", 10, 12, 9, 11), candle("2026-11-02", 11, 13, 10, 12)]
    t.ingest_schwab(db, capture(tmp_path / "m.json", "$SPX", month, kind="monthly", received_ns=mid_month))
    rows = dict(db.execute("SELECT session_date, quality FROM bars WHERE interval='1mo'"))
    assert rows == {"2026-10-01": "ok", "2026-11-02": "provisional"}
    week = [candle("2026-11-16", 10, 12, 9, 11)]  # week of Nov 16 ends Nov 22; received Nov 20
    t.ingest_schwab(db, capture(tmp_path / "w.json", "$SPX", week, kind="weekly", received_ns=mid_month))
    assert db.execute("SELECT quality FROM bars WHERE interval='1w'").fetchone() == ("provisional",)


def _five_minute(day, first="09:30", last="15:55", skip=(), price=100.0):
    rows, t0 = [], datetime.fromisoformat(f"{day}T{first}").replace(tzinfo=NY)
    end = datetime.fromisoformat(f"{day}T{last}").replace(tzinfo=NY)
    k = 0
    while t0 <= end:
        if t0.strftime("%H:%M") not in skip:
            p = price + k
            rows.append(dict(datetime=int(t0.timestamp() * 1000), open=p, high=p + 0.5, low=p - 0.5, close=p + 0.25,
                             volume=10))
        t0 += timedelta(minutes=5)
        k += 1
    return rows


def test_derived_hour_and_four_hour_bars_follow_the_session_and_skip_gaps(db, tmp_path):
    rows = _five_minute("2016-11-23") + _five_minute("2016-11-25", last="12:55") \
        + _five_minute("2016-11-28", skip=("11:00",))
    t.ingest_schwab(db, capture(tmp_path / "spy5.json", "SPY", rows, kind="minute", n=5))
    out = t.derive(db, "SPY", "schwab")
    hours = db.execute("SELECT session_date, start_ns, open, high, low, close, volume FROM bars"
                       " WHERE interval='1h' ORDER BY start_ns").fetchall()
    by_day = {}
    for r in hours:
        by_day.setdefault(r[0], []).append(r)
    assert len(by_day["2016-11-23"]) == 7                    # 09:30 ... 15:30 (last bucket is 30 minutes)
    assert len(by_day["2016-11-25"]) == 4                    # 13:00 early close: 09:30, 10:30, 11:30, 12:30
    assert len(by_day["2016-11-28"]) == 6                    # the 10:30 bucket lacks 11:00 and is skipped
    first = by_day["2016-11-23"][0]
    assert datetime.fromtimestamp(first[1] / 1e9, NY).strftime("%H:%M") == "09:30"
    assert (first[2], first[3], first[4], first[5], first[6]) == (100.0, 111.5, 99.5, 111.25, 120.0)
    four = db.execute("SELECT session_date, COUNT(*) FROM bars WHERE interval='4h' GROUP BY 1").fetchall()
    assert dict(four) == {"2016-11-23": 2, "2016-11-25": 1, "2016-11-28": 1}
    assert out["1h"]["skipped_incomplete"] == 1
    assert t.derive(db, "SPY", "schwab") == out               # rerun replaces, never duplicates
    assert db.execute("SELECT COUNT(*) FROM bars WHERE interval='1h'").fetchone()[0] == 17
    assert db.execute("SELECT COUNT(*) FROM bars WHERE quality='derived' AND interval='5m'").fetchone()[0] == 0


def test_futures_four_hour_buckets_start_at_six_pm_and_skip_the_halt(db, tmp_path):
    rows, t0 = [], datetime(2026, 11, 18, 18, 0, tzinfo=NY)
    while t0 < datetime(2026, 11, 19, 18, 0, tzinfo=NY):
        if t0.hour != 17:
            rows.append(dict(datetime=int(t0.timestamp() * 1000), open=7000, high=7001, low=6999, close=7000,
                             volume=5))
        t0 += timedelta(minutes=5)
    t.ingest_schwab(db, capture(tmp_path / "es5.json", "/ES", rows, kind="minute", n=5, returned="/ESZ26"))
    out = t.derive(db, "ES", "schwab")
    starts = [datetime.fromtimestamp(s / 1e9, NY).strftime("%H:%M") for (s,) in db.execute(
        "SELECT start_ns FROM bars WHERE interval='4h' ORDER BY start_ns")]
    assert starts == ["18:00", "22:00", "02:00", "06:00", "10:00", "14:00"]
    assert out["1h"]["bars"] == 23 and out["4h"]["bars"] == 6
