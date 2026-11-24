"""Data-driven pre-open regimes (docs/strategies/regime-lab.md).

Each regime is a leaf of a shallow regression tree (depth <= 4, so at most four
indicators define a regime) fitted on 2008-2018 sessions to predict the next
session's high-low range in expected-move units. Every indicator uses only data
through the prior close. Validation: 2019-01-02 .. 2025-11-20. Sessions after the
locked holdout start (2025-11-21) are never loaded.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from contextlib import closing
import json
import math
from pathlib import Path
from statistics import fmean, median, pstdev
import sys
from types import SimpleNamespace

from qte import truth_db
from qte.regime_study import features as legacy_features
from qte.strategies.volume import _cmf, _mfi, _weighted_price

FIT = ("2008-01-02", "2018-12-31")
VALIDATE = ("2019-01-02", "2025-11-20")
MAX_DEPTH = 4
MIN_LEAF = 120
INDICATORS = {
    "vix": "Prior VIX close",
    "vix_ts": "VIX / VIX3M (term structure; > 1 is backwardation)",
    "trend50": "(prior close - SMA50) in expected moves",
    "ma_spread": "(SMA20 - SMA50) / prior close, percent",
    "bb_pctb": "Bollinger %B (20, 2) of the prior close",
    "bb_width_pct": "Bollinger bandwidth percentile over the prior 252 sessions",
    "vwma_tilt": "SPY (VWMA20 - SMA20) / SMA20, basis points",
    "cmf20": "SPY Chaikin money flow, 20 sessions",
    "mfi14": "SPY money flow index, 14 sessions",
    "rv_iv": "10-session realized close-to-close vol / VIX",
    "ret5": "5-session SPX return in expected moves",
}


def _prior_features(prior, widths, pv, pv3, spy):
    em = prior[-1] * pv / 100 / math.sqrt(252)
    sma20, sma50 = fmean(prior[-20:]), fmean(prior[-50:])
    sd20 = pstdev(prior[-20:])
    width = 4 * sd20 / sma20
    percentile = sum(w < width for w in widths[-252:]) / min(252, len(widths))
    ybars = [SimpleNamespace(**bar) for bar in spy]
    ymean = fmean(b.close for b in ybars)
    rets = [math.log(prior[k] / prior[k-1]) for k in range(len(prior)-10, len(prior))]
    return dict(em=em, vix=pv, vix_ts=pv/pv3, trend50=(prior[-1]-sma50)/em,
                ma_spread=100*(sma20-sma50)/prior[-1],
                bb_pctb=(prior[-1]-(sma20-2*sd20))/(4*sd20) if sd20 > 0 else .5,
                bb_width_pct=percentile, vwma_tilt=1e4*(_weighted_price(ybars)-ymean)/ymean,
                cmf20=_cmf(ybars), mfi14=_mfi(ybars[-15:]),
                rv_iv=pstdev(rets)*math.sqrt(252)/(pv/100), ret5=(prior[-1]-prior[-6])/em), width


def preopen_row(db, asof):
    """Read only through the previous scheduled close, with no outcome row."""
    spx, vix, vix3m, spy = (truth_db.daily(db, k, end=asof) for k in ("SPX", "VIX", "VIX3M", "SPY"))
    if any(asof not in series for series in (spx, vix, vix3m, spy)) or len(spx) < 272 or len(spy) < 20:
        raise ValueError("Missing prior-session SPX, VIX, VIX3M or SPY history")
    closes = [b["close"] for b in spx.values()]
    widths = [4*pstdev(closes[i-20:i])/fmean(closes[i-20:i]) for i in range(len(closes)-252, len(closes))]
    row, _ = _prior_features(closes, widths, vix[asof]["close"], vix3m[asof]["close"],
                             list(spy.values())[-20:])
    return dict(row, asof=asof, anchor=closes[-1])


def build_rows(db):
    spx, vix, vix3m, spy = (truth_db.daily(db, k, end=VALIDATE[1]) for k in ("SPX", "VIX", "VIX3M", "SPY"))
    legacy = {r["date"]: r["regime"] for r in legacy_features({"SPX": spx, "VIX": vix, "SPY": spy})}
    dates = [d for d in sorted(spx) if d <= VALIDATE[1]]
    vdays, v3days, ydays = sorted(vix), sorted(vix3m), sorted(spy)
    closes = [spx[d]["close"] for d in dates]
    widths, rows = [], []
    for i, d in enumerate(dates):
        if i < 260 or d < "2007-01-01":
            if i >= 20:
                window = closes[i - 20:i]
                widths.append(4 * pstdev(window) / fmean(window))
            continue
        p = dates[i - 1]
        jv, j3, jy = (bisect_left(x, d) - 1 for x in (vdays, v3days, ydays))
        if min(jv, j3, jy) < 25 or vdays[jv] != p:
            if i >= 20:
                widths.append(4 * pstdev(closes[i - 20:i]) / fmean(closes[i - 20:i]))
            continue
        row, width = _prior_features(closes[:i], widths, vix[vdays[jv]]["close"],
                                     vix3m[v3days[j3]]["close"], [spy[x] for x in ydays[jy-19:jy+1]])
        widths.append(width)
        today = spx[d]
        em = row["em"]
        row.update(date=d, legacy=legacy.get(d, "unclassified"), close_sigma=(today["close"]-closes[i-1])/em)
        if today["quality"] != "close_only" and today["high"] > today["low"]:
            rng = today["high"] - today["low"]
            loc = (today["close"] - today["low"]) / rng
            row.update(range_sigma=rng / em, high_sigma=(today["high"] - closes[i-1]) / em,
                       low_sigma=(today["low"] - closes[i-1]) / em,
                       trend_day=rng / em >= 1 and (loc >= 0.9 or loc <= 0.1))
        if None not in (row["cmf20"], row["mfi14"]) and "range_sigma" in row:
            rows.append(row)
    return rows


# ---------------------------------------------------------------- tree

def _sse(values):
    if not values:
        return 0.0
    m = fmean(values)
    return math.fsum((v - m) ** 2 for v in values)


def fit_tree(rows, target="range_sigma", depth=MAX_DEPTH, min_leaf=MIN_LEAF, used=()):
    values = [r[target] for r in rows]
    node = dict(n=len(rows), mean=fmean(values))
    if depth == 0 or len(rows) < 2 * min_leaf:
        return node
    best = None
    base = _sse(values)
    for name in INDICATORS:
        xs = sorted(r[name] for r in rows)
        cuts = sorted({xs[int(len(xs) * q / 10)] for q in range(1, 10)})
        for cut in cuts:
            left = [r[target] for r in rows if r[name] <= cut]
            right = [r[target] for r in rows if r[name] > cut]
            if len(left) < min_leaf or len(right) < min_leaf:
                continue
            gain = base - _sse(left) - _sse(right)
            if best is None or gain > best[0]:
                best = (gain, name, cut)
    if best is None or best[0] <= 0.002 * base:  # require at least 0.2% of node variance explained
        return node
    _, name, cut = best
    node.update(split=name, cut=round(cut, 4),
                left=fit_tree([r for r in rows if r[name] <= cut], target, depth - 1, min_leaf, used + (name,)),
                right=fit_tree([r for r in rows if r[name] > cut], target, depth - 1, min_leaf, used + (name,)))
    return node


def leaves(node, path=()):
    if "split" not in node:
        return [path]
    return (leaves(node["left"], path + ((node["split"], "<=", node["cut"]),))
            + leaves(node["right"], path + ((node["split"], ">", node["cut"]),)))


def matches(row, path):
    return all(row[n] <= c if op == "<=" else row[n] > c for n, op, c in path)


def describe(group):
    if not group:
        return None
    cs = [r["close_sigma"] for r in group]
    sd = pstdev(cs) if len(cs) > 1 else 0.0
    return dict(
        sessions=len(group), range_sigma=round(fmean(r["range_sigma"] for r in group), 3),
        median_range_sigma=round(median(r["range_sigma"] for r in group), 3),
        close_beyond_1sigma=round(sum(abs(c) > 1 for c in cs) / len(cs), 3),
        close_beyond_2sigma=round(sum(abs(c) > 2 for c in cs) / len(cs), 3),
        touch_1sigma=round(sum(r["high_sigma"] >= 1 or r["low_sigma"] <= -1 for r in group) / len(group), 3),
        mean_close_sigma=round(fmean(cs), 3),
        close_sigma_t=round(fmean(cs) / (sd / math.sqrt(len(cs))), 2) if sd > 0 else None,
        up_close=round(sum(c > 0 for c in cs) / len(cs), 3),
        trend_day=round(sum(r["trend_day"] for r in group) / len(group), 3),
        worst_close_sigma=round(min(cs), 2))


def r2(groups_fit, groups_val, target="range_sigma"):
    """Out-of-sample R^2 of predicting each validation session by its group's fit-period mean."""
    allv = [r[target] for g in groups_val.values() for r in g]
    mean = fmean(allv)
    sst = math.fsum((v - mean) ** 2 for v in allv)
    sse = 0.0
    for key, g in groups_val.items():
        pred = fmean(r[target] for r in groups_fit[key]) if groups_fit.get(key) else mean
        sse += math.fsum((r[target] - pred) ** 2 for r in g)
    return round(1 - sse / sst, 4)


def rank_corr(a, b):
    def ranks(v):
        order = sorted(range(len(v)), key=v.__getitem__)
        out = [0.0] * len(v)
        for rank, i in enumerate(order):
            out[i] = rank
        return out
    ra, rb = ranks(a), ranks(b)
    ma, mb = fmean(ra), fmean(rb)
    num = math.fsum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = math.sqrt(math.fsum((x - ma) ** 2 for x in ra) * math.fsum((y - mb) ** 2 for y in rb))
    return round(num / den, 3) if den else None


def run(db):
    rows = build_rows(db)
    fit = [r for r in rows if FIT[0] <= r["date"] <= FIT[1]]
    val = [r for r in rows if VALIDATE[0] <= r["date"] <= VALIDATE[1]]
    tree = fit_tree(fit)
    paths = leaves(tree)
    regimes = []
    for k, path in enumerate(sorted(paths, key=lambda p: fmean(r["range_sigma"] for r in fit if matches(r, p)))):
        f = [r for r in fit if matches(r, path)]
        v = [r for r in val if matches(r, path)]
        regimes.append(dict(id=f"R{k + 1:02d}", rules=[f"{n} {op} {c}" for n, op, c in path],
                            indicators=sorted({n for n, _, _ in path}), fit=describe(f), validation=describe(v)))
    by_leaf_fit = {r["id"]: [x for x in fit if matches(x, p)] for r, p in zip(regimes, sorted(
        paths, key=lambda p: fmean(x["range_sigma"] for x in fit if matches(x, p))))}
    by_leaf_val = {r["id"]: [x for x in val if matches(x, p)] for r, p in zip(regimes, sorted(
        paths, key=lambda p: fmean(x["range_sigma"] for x in fit if matches(x, p))))}

    def grouped(rs, key):
        out = {}
        for r in rs:
            out.setdefault(key(r), []).append(r)
        return out

    def vix_bucket(r): return min(int(r["vix"] // 5), 7)
    comparison = dict(
        new_regimes_r2=r2(by_leaf_fit, by_leaf_val),
        legacy_9_labels_r2=r2(grouped(fit, lambda r: r["legacy"]), grouped(val, lambda r: r["legacy"])),
        vix_buckets_r2=r2(grouped(fit, vix_bucket), grouped(val, vix_bucket)),
        new_regimes_tail_r2=r2(by_leaf_fit, by_leaf_val, "close_sigma") if False else None,
        rank_corr_fit_vs_validation_range=rank_corr([x["fit"]["range_sigma"] for x in regimes],
                                                    [x["validation"]["range_sigma"] if x["validation"] else 0
                                                     for x in regimes]))
    return dict(schema="qte-regime-lab-v1", fit_period=FIT, validation_period=VALIDATE,
                fit_sessions=len(fit), validation_sessions=len(val), indicators=INDICATORS,
                max_depth=MAX_DEPTH, min_leaf=MIN_LEAF, target="next-session (high-low)/expected move",
                all_fit=describe(fit), all_validation=describe(val), regimes=regimes, comparison=comparison,
                tree=tree)


def classify(row, report):
    """Assign a feature row to its regime id using the saved report rules."""
    for regime in report["regimes"]:
        ok = True
        for rule in regime["rules"]:
            name, op, cut = rule.split()
            ok &= row[name] <= float(cut) if op == "<=" else row[name] > float(cut)
        if ok:
            return regime["id"]
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    with closing(truth_db.connect()) as db:
        report = run(db)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "report.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ("fit_sessions", "validation_sessions", "comparison")}, indent=1))
    for r in report["regimes"]:
        f, v = r["fit"], r["validation"] or {}
        print(r["id"], "|", " & ".join(r["rules"]))
        print(f"     fit n={f['sessions']} range={f['range_sigma']} >1σ={f['close_beyond_1sigma']} >2σ={f['close_beyond_2sigma']} "
              f"drift={f['mean_close_sigma']}(t={f['close_sigma_t']}) trend={f['trend_day']} | val n={v.get('sessions')} "
              f"range={v.get('range_sigma')} >1σ={v.get('close_beyond_1sigma')} >2σ={v.get('close_beyond_2sigma')} "
              f"drift={v.get('mean_close_sigma')}(t={v.get('close_sigma_t')}) trend={v.get('trend_day')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
