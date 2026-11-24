"""Strategy search harness: C++ sweeps over truth-database bars, every trial logged.

Workflow (enforced):
- Each dataset (instrument:interval:provider) gets a split file, written once and
  then locked: ``dev`` (search), ``validation`` (ranking) and ``holdout`` (final,
  human-only). Tools available to agents can never read holdout sessions.
- Every evaluated spec is recorded in the trial registry with its dev and
  validation results. The leaderboard deflates Sharpe ratios by the number of
  distinct specs tried on that dataset (Bailey & Lopez de Prado), so searching
  more makes it harder, not easier, to look good.
- ``holdout`` runs one recorded trial on the holdout once per spec, and only
  with an explicit confirmation flag. Its result is recorded permanently.

The C++ kernel (``qte._core.sweep_many``) is a screening simulator, not the
accounting authority; see include/qte/research/sweep.hpp for timing and fills.
"""
from __future__ import annotations

import argparse
from array import array
from bisect import bisect_left
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import itertools
import json
import math
from pathlib import Path
import sqlite3
from statistics import NormalDist, fmean, pstdev
import sys
import time
from zoneinfo import ZoneInfo

from qte import _core
from qte import truth_db
from qte.regime_study import REGIMES, features

NY = ZoneInfo("America/New_York")
SEARCH_DIR = Path("build/search")
MAX_GRID = 50_000

# Futures cost presets. spy_as_es expresses SPY bars in ES terms: SPY $1 ~ 10 SPX
# points x $50 = $500, one ES tick (0.25 pt) ~ $0.025 in SPY.
COSTS = {
    "es": dict(point_value=50.0, tick_size=0.25, commission_per_side=2.25, slippage_ticks=1.0),
    "mes": dict(point_value=5.0, tick_size=0.25, commission_per_side=0.85, slippage_ticks=1.0),
    "spy_as_es": dict(point_value=500.0, tick_size=0.025, commission_per_side=2.25, slippage_ticks=1.0),
    # Diagnostic only: same scale as spy_as_es with zero commission and slippage. Separates
    # "the signal has no edge" from "costs consume the edge". Ranked on its own leaderboard.
    "spy_as_es_frictionless": dict(point_value=500.0, tick_size=0.025, commission_per_side=0.0, slippage_ticks=0.0),
}
DIAGNOSTIC_COSTS = {"spy_as_es_frictionless"}
# Anchor (prior close) source for each traded series; VIX supplies the expected move.
ANCHORS = {"ES": ("ES", "schwab"), "SPY": ("SPY", "schwab"), "SPX": ("SPX", "schwab"), "QQQ": ("QQQ", "schwab")}

SPEC_FIELDS = {
    "bound_sigma": float, "touch_tolerance_sigma": float, "band_window": int, "band_width": float,
    "touch_lookback": int, "stress_latch": bool, "range_bars": int, "max_range_sigma": float,
    "volume_ratio": float, "volume_lookback": int, "last_entry_bar": int, "stop_at_range_mid": bool,
    "hold_bars": int, "stop_sigma": float, "target_sigma": float, "max_trades_per_session": int,
    "flatten_bars_before_close": int, "min_prior_vix": float, "max_prior_vix": float,
}
REGIME_LISTS = {"regimes": "allowed_regimes", "long_regimes": "allowed_regimes_long",
                "short_regimes": "allowed_regimes_short"}
FAMILIES = {"sigma_reversal": _core.SweepFamily.sigma_reversal,
            "opening_range_breakout": _core.SweepFamily.opening_range_breakout}
SIDES = {"long_only": _core.SweepSides.long_only, "short_only": _core.SweepSides.short_only,
         "both": _core.SweepSides.both}


class SearchError(ValueError):
    pass


# ------------------------------------------------------------------ specs

def normalize_spec(spec):
    """Validate an agent-supplied spec dict and return its canonical form."""
    if not isinstance(spec, dict):
        raise SearchError("spec must be a JSON object")
    allowed = {"family", "sides", *REGIME_LISTS, *SPEC_FIELDS}
    unknown = set(spec) - allowed
    if unknown:
        raise SearchError(f"unknown spec fields: {sorted(unknown)}")
    if spec.get("family") not in FAMILIES:
        raise SearchError(f"family must be one of {sorted(FAMILIES)}")
    out = {"family": spec["family"], "sides": spec.get("sides", "both")}
    if out["sides"] not in SIDES:
        raise SearchError(f"sides must be one of {sorted(SIDES)}")
    for key in REGIME_LISTS:
        if key in spec:
            regimes = spec[key]
            if not isinstance(regimes, list) or not regimes or set(regimes) - set(REGIMES):
                raise SearchError(f"{key} must be a nonempty list drawn from {list(REGIMES)}")
            out[key] = sorted(set(regimes), key=REGIMES.index)
    for name, kind in SPEC_FIELDS.items():
        if name in spec:
            value = spec[name]
            if kind is bool:
                if type(value) is not bool:
                    raise SearchError(f"{name} must be true or false")
            elif kind is int:
                if type(value) is not int:
                    raise SearchError(f"{name} must be an integer")
            elif type(value) not in (int, float) or not math.isfinite(value):
                raise SearchError(f"{name} must be a finite number")
            out[name] = kind(value)
    to_kernel(out)  # C++ validation of ranges
    return out


def spec_hash(spec):
    return sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def to_kernel(spec):
    k = _core.SweepSpec()
    k.family = FAMILIES[spec["family"]]
    k.sides = SIDES[spec["sides"]]
    for key, attr in REGIME_LISTS.items():
        if key in spec:
            setattr(k, attr, sum(1 << REGIMES.index(r) for r in spec[key]))
    for name in SPEC_FIELDS:
        if name in spec:
            setattr(k, name, spec[name])
    _core.sweep_validate_spec(k)  # C++ range checks (raises InvalidSweepInput, a ValueError)
    return k


def expand_grid(base, grid):
    """Cartesian product of ``grid`` (field -> list) over ``base``; each result normalized."""
    if not isinstance(grid, dict):
        raise SearchError("grid must be a JSON object of field -> list of values")
    keys = sorted(grid)
    for key in keys:
        if not isinstance(grid[key], list) or not grid[key]:
            raise SearchError(f"grid[{key!r}] must be a nonempty list")
    total = math.prod(len(grid[k]) for k in keys) if keys else 1
    if total > MAX_GRID:
        raise SearchError(f"grid has {total} combinations; limit is {MAX_GRID}")
    specs, seen = [], set()
    for combo in itertools.product(*(grid[k] for k in keys)):
        spec = normalize_spec({**base, **dict(zip(keys, combo))})
        h = spec_hash(spec)
        if h not in seen:
            seen.add(h)
            specs.append(spec)
    return specs


# ------------------------------------------------------------------ datasets

@dataclass
class Split:
    dataset: str
    sessions: list            # all session dates in the dataset, sorted
    dev_end: str
    validation_end: str
    created_ns: int

    def part(self, name):
        if name == "dev":
            return [d for d in self.sessions if d <= self.dev_end]
        if name == "validation":
            return [d for d in self.sessions if self.dev_end < d <= self.validation_end]
        if name == "holdout":
            return [d for d in self.sessions if d > self.validation_end]
        raise SearchError(f"unknown split {name!r}")


def parse_dataset(key):
    parts = key.split(":")
    if len(parts) != 3:
        raise SearchError("dataset must be INSTRUMENT:INTERVAL:PROVIDER, e.g. SPY:5m:alpaca")
    return tuple(parts)


def _in_cash_hours(start_ns):
    t = datetime.fromtimestamp(start_ns / 1e9, NY).time()
    return (t.hour, t.minute) >= (9, 30) and t.hour < 16


def load_sessions(db, dataset):
    """Return {session_date: [bar rows]} restricted to the regular session."""
    instrument, interval, provider = parse_dataset(dataset)
    sql = ("SELECT start_ns, session_date, open, high, low, close, volume FROM bars WHERE instrument=? AND"
           " interval=? AND provider=? AND quality != 'provisional'")
    args = [instrument, interval, provider]
    if instrument not in truth_db.FUTURES:
        sql += " AND session='rth'"
    out = {}
    for row in db.execute(sql + " ORDER BY start_ns", args):
        if instrument in truth_db.FUTURES and not _in_cash_hours(row[0]):
            continue
        out.setdefault(row[1], []).append(row)
    if not out:
        raise SearchError(f"no bars for {dataset} in the truth database")
    return out


def session_contexts(db, dataset, dates):
    """Prior-day anchor, expected move, prior VIX and pre-open regime index for each date."""
    instrument = parse_dataset(dataset)[0]
    anchor_instrument, anchor_provider = ANCHORS.get(instrument, (instrument, "schwab"))
    closes = truth_db.daily(db, anchor_instrument, provider=anchor_provider)
    vix = truth_db.daily(db, "VIX")
    regimes = {r["date"]: r["regime"] for r in features(
        {"SPX": truth_db.daily(db, "SPX"), "VIX": vix, "SPY": truth_db.daily(db, "SPY")})}
    close_days, vix_days = sorted(closes), sorted(vix)
    out = {}
    for d in dates:
        i, j = bisect_left(close_days, d) - 1, bisect_left(vix_days, d) - 1
        if i < 0 or j < 0:
            continue
        anchor, prior_vix = closes[close_days[i]]["close"], vix[vix_days[j]]["close"]
        out[d] = (anchor, anchor * prior_vix / 100 / math.sqrt(252), prior_vix,
                  REGIMES.index(regimes.get(d, "unclassified")))
    return out


def build_kernel_data(sessions, contexts, dates, interval):
    seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800}[interval]
    start, o, h, l, c, v, sid = array("q"), array("d"), array("d"), array("d"), array("d"), array("d"), array("i")
    ctx_list, used = [], []
    for d in dates:
        if d not in contexts or d not in sessions:
            continue
        k = len(ctx_list)
        ctx_list.append(_core.SweepSession(*contexts[d]))
        used.append(d)
        for row in sessions[d]:
            start.append(row[0]); o.append(row[2]); h.append(row[3]); l.append(row[4]); c.append(row[5])
            v.append(row[6] or 0.0); sid.append(k)
    bars = _core.SweepBars(seconds * 1_000_000_000, list(start), list(o), list(h), list(l), list(c), list(v), list(sid))
    return bars, ctx_list, used


def split_path(dataset):
    return SEARCH_DIR / "splits" / (dataset.replace(":", "_") + ".json")


def get_split(db, dataset, *, create=True, holdout_sessions=252, validation_sessions=252):
    """Load the locked split for a dataset, creating it once from the sessions available now."""
    path = split_path(dataset)
    if path.exists():
        doc = json.loads(path.read_text())
        return Split(doc["dataset"], doc["sessions"], doc["dev_end"], doc["validation_end"], doc["created_ns"])
    if not create:
        raise SearchError(f"no split for {dataset}; run describe first")
    dates = sorted(load_sessions(db, dataset))
    if len(dates) < holdout_sessions + validation_sessions + 100:
        raise SearchError(f"{dataset} has {len(dates)} sessions; need at least "
                          f"{holdout_sessions + validation_sessions + 100} for dev/validation/holdout")
    validation_end = dates[-holdout_sessions - 1]
    dev_end = dates[-holdout_sessions - validation_sessions - 1]
    split = Split(dataset, dates, dev_end, validation_end, time.time_ns())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as stream:  # never overwritten; delete by hand to re-split
        json.dump(dict(dataset=dataset, sessions=dates, dev_end=dev_end, validation_end=validation_end,
                       created_ns=split.created_ns, holdout_start=dates[-holdout_sessions]), stream)
    return split


# ------------------------------------------------------------------ registry

REGISTRY_SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
  id INTEGER PRIMARY KEY, created_ns INTEGER NOT NULL, agent TEXT NOT NULL, dataset TEXT NOT NULL,
  costs TEXT NOT NULL, spec_hash TEXT NOT NULL, spec_json TEXT NOT NULL,
  dev_trades INTEGER, dev_net REAL, dev_sharpe REAL, dev_max_dd REAL, dev_win REAL, dev_pf REAL,
  val_trades INTEGER, val_net REAL, val_sharpe REAL, val_max_dd REAL, val_win REAL, val_pf REAL,
  val_session_pnl TEXT NOT NULL,
  dev_session_pnl TEXT
);
CREATE INDEX IF NOT EXISTS trials_by_dataset ON trials(dataset, costs, spec_hash);
CREATE TABLE IF NOT EXISTS holdout (
  dataset TEXT NOT NULL, costs TEXT NOT NULL, spec_hash TEXT NOT NULL, trial_id INTEGER NOT NULL,
  created_ns INTEGER NOT NULL, result_json TEXT NOT NULL, PRIMARY KEY (dataset, costs, spec_hash)
);
"""


def registry(path=None):
    path = Path(path or SEARCH_DIR / "trials.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(REGISTRY_SCHEMA)
    columns = {row[1] for row in db.execute("PRAGMA table_info(trials)")}
    if "dev_session_pnl" not in columns:  # registries created before 2026-11-24
        db.execute("ALTER TABLE trials ADD COLUMN dev_session_pnl TEXT")
    return db


def _metrics(result):
    return (result.trade_count, round(result.net_pnl, 2), round(result.sharpe, 4), round(result.max_drawdown, 2),
            round(result.win_rate, 4), round(result.profit_factor, 4))


def summary(row):
    keys = ("trades", "net_pnl", "sharpe", "max_drawdown", "win_rate", "profit_factor")
    return dict(zip(keys, row))


# ------------------------------------------------------------------ evaluation

class Workspace:
    """Loaded dataset with dev and validation kernel inputs (holdout only on explicit request)."""

    def __init__(self, truth, dataset):
        self.dataset = dataset
        self.split = get_split(truth, dataset)
        self.sessions = load_sessions(truth, dataset)
        self.contexts = session_contexts(truth, dataset, self.split.sessions)
        interval = parse_dataset(dataset)[1]
        self.parts = {name: build_kernel_data(self.sessions, self.contexts, self.split.part(name), interval)
                      for name in ("dev", "validation")}
        self._interval = interval
        self._truth = truth

    def holdout(self):
        return build_kernel_data(self.sessions, self.contexts, self.split.part("holdout"), self._interval)

    def describe(self):
        info = {}
        for name, (bars, _, used) in self.parts.items():
            info[name] = dict(sessions=len(used), bars=bars.size, first=used[0] if used else None,
                              last=used[-1] if used else None)
        info["holdout"] = dict(sessions=len(self.split.part("holdout")), locked=True)
        return dict(dataset=self.dataset, dev_end=self.split.dev_end, validation_end=self.split.validation_end,
                    parts=info, families=sorted(FAMILIES), sides=sorted(SIDES), regimes=list(REGIMES),
                    spec_fields={k: v.__name__ for k, v in SPEC_FIELDS.items()}, costs=COSTS)


def costs_for(name):
    if name not in COSTS:
        raise SearchError(f"costs must be one of {sorted(COSTS)}")
    return _core.FuturesCosts(**COSTS[name])


SORT_KEYS = {
    "validation": lambda r: -r["validation"]["sharpe"],
    "dev": lambda r: -r["dev"]["sharpe"],
    "consistent": lambda r: -min(r["dev"]["sharpe"], r["validation"]["sharpe"]),
}


def evaluate(ws, specs, *, costs="es", agent="unknown", reg=None, threads=0, sort_by="consistent"):
    """Run specs on dev and validation, log every trial, return rows sorted by ``sort_by``.

    ``consistent`` (default) ranks by the weaker of dev and validation Sharpe, so a spec
    must hold up in both periods to rank well."""
    if sort_by not in SORT_KEYS:
        raise SearchError(f"sort_by must be one of {sorted(SORT_KEYS)}")
    fc = costs_for(costs)
    kernels = [to_kernel(s) for s in specs]
    dev_bars, dev_ctx, _ = ws.parts["dev"]
    val_bars, val_ctx, _ = ws.parts["validation"]
    dev = _core.sweep_many(dev_bars, dev_ctx, fc, kernels, threads)
    val = _core.sweep_many(val_bars, val_ctx, fc, kernels, threads)
    now = time.time_ns()
    dev_dates = ws.parts["dev"][2]
    dev_regimes = [REGIMES[ws.contexts[day][3]] for day in dev_dates]
    rows = []
    with closing(reg or registry()) as db, db:
        for spec, d, v in zip(specs, dev, val):
            h = spec_hash(spec)
            cur = db.execute(
                "INSERT INTO trials(created_ns, agent, dataset, costs, spec_hash, spec_json, dev_trades, dev_net,"
                " dev_sharpe, dev_max_dd, dev_win, dev_pf, val_trades, val_net, val_sharpe, val_max_dd, val_win,"
                " val_pf, val_session_pnl, dev_session_pnl) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, agent, ws.dataset, costs, h, json.dumps(spec, sort_keys=True), *_metrics(d), *_metrics(v),
                 json.dumps([round(x, 2) for x in v.session_pnl]), json.dumps([round(x, 2) for x in d.session_pnl])))
            by_year, by_regime = {}, {}
            for day, regime, pnl in zip(dev_dates, dev_regimes, d.session_pnl):
                by_year[day[:4]] = by_year.get(day[:4], 0.0) + pnl
                if pnl:
                    by_regime[regime] = by_regime.get(regime, 0.0) + pnl
            gross = sum(t.pnl for t in d.trades)  # net; gross adds back per-trade costs below
            per_trade_cost = 2 * fc.commission_per_side + 2 * fc.slippage_ticks * fc.tick_size * fc.point_value
            rows.append(dict(trial_id=cur.lastrowid, spec_hash=h, spec=spec, dev=summary(_metrics(d)),
                             validation=summary(_metrics(v)),
                             dev_net_by_year={k: round(x) for k, x in sorted(by_year.items())},
                             dev_net_by_regime={k: round(x) for k, x in sorted(by_regime.items())},
                             dev_gross_before_costs=round(gross + per_trade_cost * d.trade_count, 2)))
    rows.sort(key=lambda r: (SORT_KEYS[sort_by](r), r["spec_hash"]))
    return rows


def deflated_sharpe(sharpe_annual, session_pnl, n_trials, sharpe_variance_annual):
    """Probability the true Sharpe exceeds the best expected from ``n_trials`` unskilled tries."""
    t = len(session_pnl)
    if t < 3 or n_trials < 1:
        return None
    sr = sharpe_annual / math.sqrt(252)
    var = max(sharpe_variance_annual / 252, 0.0)
    nd, gamma = NormalDist(), 0.5772156649
    if n_trials > 1 and var > 0:
        sr0 = math.sqrt(var) * ((1 - gamma) * nd.inv_cdf(1 - 1 / n_trials)
                                + gamma * nd.inv_cdf(1 - 1 / (n_trials * math.e)))
    else:
        sr0 = 0.0
    mean, sd = fmean(session_pnl), pstdev(session_pnl)
    if sd == 0:
        return None
    skew = fmean(((x - mean) / sd) ** 3 for x in session_pnl)
    kurt = fmean(((x - mean) / sd) ** 4 for x in session_pnl)
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return None
    return nd.cdf((sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom))


def _sharpe(pnl):
    if len(pnl) < 2:
        return 0.0
    mean, sd = fmean(pnl), pstdev(pnl) * math.sqrt(len(pnl) / (len(pnl) - 1))
    return mean / sd * math.sqrt(252) if sd > 0 else 0.0


def leaderboard(dataset, *, costs="es", top=20, min_trades=30, require_positive_dev=True, reg=None):
    """Rank specs by deflated Sharpe probability over dev + validation combined.

    Each spec's daily P&L across both periods (about 2,500 sessions for SPY) is
    scored, deflated by the number of distinct specs tried and the spread of their
    combined Sharpe ratios. By default specs must be profitable on dev and validation
    separately. Trials recorded before dev P&L was stored fall back to validation only
    and are flagged."""
    with closing(reg or registry()) as db:
        rows = db.execute("SELECT id, agent, spec_hash, spec_json, dev_trades, dev_net, dev_sharpe, val_trades,"
                          " val_net, val_sharpe, val_max_dd, val_win, val_pf, val_session_pnl, dev_session_pnl"
                          " FROM trials WHERE dataset=? AND costs=? ORDER BY id", (dataset, costs)).fetchall()
        held = {h: json.loads(r) for h, r in db.execute(
            "SELECT spec_hash, result_json FROM holdout WHERE dataset=? AND costs=?", (dataset, costs))}
    latest = {}
    for r in rows:
        if r[2] not in latest or (r[14] is not None or latest[r[2]][14] is None):
            latest[r[2]] = r  # identical specs are deterministic; prefer records with dev P&L
    n_trials = len(latest)
    scored = {}
    for h, r in latest.items():
        val = json.loads(r[13])
        combined = (json.loads(r[14]) + val) if r[14] else val
        scored[h] = (combined, _sharpe(combined), r[14] is not None)
    sharpes = [x[1] for x in scored.values()]
    var = pstdev(sharpes) ** 2 if len(sharpes) > 1 else 0.0
    board = []
    for h, r in latest.items():
        combined, combined_sharpe, full = scored[h]
        if r[7] < min_trades or (require_positive_dev and (r[6] <= 0 or r[9] <= 0 or not full)):
            continue
        board.append(dict(
            trial_id=r[0], agent=r[1], spec_hash=h, spec=json.loads(r[3]),
            dev=dict(trades=r[4], net_pnl=r[5], sharpe=r[6]),
            validation=dict(trades=r[7], net_pnl=r[8], sharpe=r[9], max_drawdown=r[10], win_rate=r[11],
                            profit_factor=r[12]),
            combined_sharpe=round(combined_sharpe, 4), scored_sessions=len(combined),
            scored_on="dev+validation" if full else "validation only (legacy trial)",
            deflated_sharpe_probability=deflated_sharpe(combined_sharpe, combined, n_trials, var),
            holdout=held.get(h)))
    board.sort(key=lambda b: (-(b["deflated_sharpe_probability"] or 0), -b["combined_sharpe"], b["spec_hash"]))
    return dict(dataset=dataset, costs=costs, distinct_specs_tried=n_trials, min_validation_trades=min_trades,
                require_positive_dev=require_positive_dev,
                note="Deflated probability over dev+validation daily P&L, penalized by the number of specs tried. "
                     "Holdout results are human-run only.",
                leaders=board[:top])


def run_holdout(ws, trial_id, *, reg=None):
    with closing(reg or registry()) as db, db:
        row = db.execute("SELECT spec_hash, spec_json, costs FROM trials WHERE id=? AND dataset=?",
                         (trial_id, ws.dataset)).fetchone()
        if row is None:
            raise SearchError("unknown trial for this dataset")
        h, spec_json, costs = row
        if db.execute("SELECT 1 FROM holdout WHERE dataset=? AND costs=? AND spec_hash=?",
                      (ws.dataset, costs, h)).fetchone():
            raise SearchError("this spec has already been evaluated on the holdout; results are final")
        bars, ctx, used = ws.holdout()
        result = _core.sweep_run(bars, ctx, costs_for(costs), to_kernel(json.loads(spec_json)))
        out = dict(summary(_metrics(result)), sessions=len(used), first=used[0] if used else None,
                   last=used[-1] if used else None)
        db.execute("INSERT INTO holdout VALUES (?,?,?,?,?,?)",
                   (ws.dataset, costs, h, trial_id, time.time_ns(), json.dumps(out, sort_keys=True)))
    return out


# ------------------------------------------------------------------ agent tool adapter

AGENT_TOOLS = ("qte_describe", "qte_sweep", "qte_leaderboard")  # deliberately no holdout access


def agent_tool(envelope, *, truth_path=truth_db.DEFAULT_DB):
    """Glass-agents stdin envelope -> compact JSON. Dataset/costs come from operator-fixed context."""
    tool, given, ctx = envelope.get("tool"), envelope.get("input") or {}, envelope.get("context") or {}
    if tool not in AGENT_TOOLS:
        raise SearchError(f"tool must be one of {AGENT_TOOLS}")
    dataset, costs = ctx.get("dataset"), ctx.get("costs", "es")
    if given.get("costs") is not None:  # agents may only switch to a diagnostic preset
        if given["costs"] not in DIAGNOSTIC_COSTS:
            raise SearchError(f"input.costs may only be one of {sorted(DIAGNOSTIC_COSTS)}")
        costs = given["costs"]
    agent = str(ctx.get("agent", "glass-agents"))[:64]
    if not dataset:
        raise SearchError("context.dataset is required (set by the operator in agents.json)")
    if set(given) - {"spec", "grid", "top", "min_trades", "costs", "sort_by"}:
        raise SearchError("unknown input fields")
    top = min(int(given.get("top", 10)), 25)
    if tool == "qte_leaderboard":
        return leaderboard(dataset, costs=costs, top=top, min_trades=int(given.get("min_trades", 30)))
    with closing(truth_db.connect(truth_path)) as truth:
        ws = Workspace(truth, dataset)
        if tool == "qte_describe":
            return ws.describe()
        started = time.perf_counter()
        specs = expand_grid(given.get("spec") or {}, given.get("grid") or {})
        rows = evaluate(ws, specs, costs=costs, agent=agent, sort_by=given.get("sort_by", "consistent"))
    output_dir = envelope.get("output_dir")
    if output_dir:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        (Path(output_dir) / "all_results.json").write_text(json.dumps(rows))
    return dict(dataset=dataset, costs=costs, evaluated=len(rows), seconds=round(time.perf_counter() - started, 2),
                sort_by=given.get("sort_by", "consistent"), top=rows[:top],
                note="Validation is shared across all agents' trials; check qte_leaderboard for deflated rankings.")


# ------------------------------------------------------------------ CLI

def _json_arg(text):
    path = Path(text)
    if not text.lstrip().startswith(("{", "[")) and path.is_file():
        text = path.read_text()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SearchError(f"invalid JSON: {exc}") from None


def main(argv=None):
    parser = argparse.ArgumentParser(prog="qte search", description=__doc__.splitlines()[0])
    parser.add_argument("--truth-db", type=Path, default=truth_db.DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("describe", help="Dataset split, sizes and the spec vocabulary (creates the split once)")
    d.add_argument("--dataset", required=True)
    s = sub.add_parser("sweep", help="Evaluate a base spec over a parameter grid on dev and validation")
    s.add_argument("--dataset", required=True)
    s.add_argument("--spec", required=True, help="JSON object or file: base spec")
    s.add_argument("--grid", default="{}", help="JSON object or file: field -> list of values")
    s.add_argument("--costs", default="es", choices=sorted(COSTS))
    s.add_argument("--agent", default="operator")
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--threads", type=int, default=0)
    s.add_argument("--sort-by", default="consistent", choices=sorted(SORT_KEYS))
    lb = sub.add_parser("leaderboard", help="Best specs by deflated validation Sharpe")
    lb.add_argument("--dataset", required=True)
    lb.add_argument("--costs", default="es", choices=sorted(COSTS))
    lb.add_argument("--top", type=int, default=20)
    lb.add_argument("--min-trades", type=int, default=30)
    lb.add_argument("--include-negative-dev", action="store_true", help="Also list specs that lose on dev")
    sub.add_parser("agent-tool", help="Glass-agents adapter: JSON envelope on stdin, JSON result on stdout")
    ho = sub.add_parser("holdout", help="HUMAN ONLY: evaluate one trial on the locked holdout, once")
    ho.add_argument("--dataset", required=True)
    ho.add_argument("--trial", type=int, required=True)
    ho.add_argument("--confirm", action="store_true", help="Acknowledge this spends the holdout for this spec")
    args = parser.parse_args(argv)
    try:
        if args.command == "agent-tool":
            print(json.dumps(agent_tool(json.loads(sys.stdin.read()), truth_path=args.truth_db)))
            return 0
        if args.command == "leaderboard":
            print(json.dumps(leaderboard(args.dataset, costs=args.costs, top=args.top, min_trades=args.min_trades,
                                         require_positive_dev=not args.include_negative_dev), indent=1))
            return 0
        with closing(truth_db.connect(args.truth_db)) as truth:
            ws = Workspace(truth, args.dataset)
            if args.command == "describe":
                print(json.dumps(ws.describe(), indent=1))
            elif args.command == "sweep":
                started = time.perf_counter()
                specs = expand_grid(_json_arg(args.spec), _json_arg(args.grid))
                rows = evaluate(ws, specs, costs=args.costs, agent=args.agent, threads=args.threads,
                                sort_by=args.sort_by)
                print(json.dumps(dict(dataset=args.dataset, costs=args.costs, evaluated=len(rows),
                                      seconds=round(time.perf_counter() - started, 2), sort_by=args.sort_by,
                                      top=rows[:args.top]), indent=1))
            else:
                if not args.confirm:
                    parser.error("holdout requires --confirm; it can be run once per spec")
                print(json.dumps(run_holdout(ws, args.trial), indent=1))
        return 0
    except (SearchError, truth_db.TruthError, sqlite3.Error, ValueError, OSError) as exc:
        print(json.dumps(dict(error=f"{type(exc).__name__}: {exc}")), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
