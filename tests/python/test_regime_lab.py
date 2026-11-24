import random

from qte import regime_lab as rl


def synthetic_rows(n=1200, seed=0):
    rng, rows = random.Random(seed), []
    for i in range(n):
        r = {name: rng.random() for name in rl.INDICATORS}
        base = 0.7 if r["vix_ts"] < 0.5 else 1.3
        r.update(range_sigma=base + (0.3 if r["bb_pctb"] > 0.8 else 0.0) + rng.gauss(0, 0.05),
                 date=f"2010-01-{i:04d}")
        rows.append(r)
    return rows


def test_tree_recovers_the_informative_indicators_and_caps_depth():
    tree = rl.fit_tree(synthetic_rows(), depth=4, min_leaf=60)
    assert tree["split"] == "vix_ts"
    paths = rl.leaves(tree)
    assert all(len({n for n, _, _ in p}) <= 4 for p in paths)
    used = {n for p in paths for n, _, _ in p}
    assert {"vix_ts", "bb_pctb"} <= used


def test_every_row_falls_in_exactly_one_regime_and_classify_agrees():
    rows = synthetic_rows()
    tree = rl.fit_tree(rows, depth=3, min_leaf=60)
    paths = rl.leaves(tree)
    report = {"regimes": [{"id": f"R{i}", "rules": [f"{n} {op} {c}" for n, op, c in p]} for i, p in enumerate(paths)]}
    for r in rows[:200]:
        hits = [i for i, p in enumerate(paths) if rl.matches(r, p)]
        assert len(hits) == 1 and rl.classify(r, report) == f"R{hits[0]}"


def test_out_of_sample_r2_and_rank_correlation():
    rows = synthetic_rows()
    fit, val = rows[:600], rows[600:]
    tree = rl.fit_tree(fit, depth=2, min_leaf=60)
    paths = rl.leaves(tree)
    gf = {i: [r for r in fit if rl.matches(r, p)] for i, p in enumerate(paths)}
    gv = {i: [r for r in val if rl.matches(r, p)] for i, p in enumerate(paths)}
    assert rl.r2(gf, gv) > 0.8
    assert rl.rank_corr([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert rl.rank_corr([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0


def test_build_rows_features_use_only_prior_sessions(monkeypatch):
    from datetime import date, timedelta
    rng, days, d = random.Random(5), [], date(2007, 1, 1)
    while len(days) < 700:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)

    def series(base, vol, volume=0.0):
        out, p = {}, base
        for x in days:
            o = p
            p = max(1.0, p * (1 + rng.gauss(0, vol)))
            out[x] = dict(date=x, open=o, high=max(o, p) * 1.003, low=min(o, p) * 0.997, close=p,
                          volume=volume or 1e6 + rng.random() * 1e5, quality="ok")
        return out
    data = {"SPX": series(4000, 0.01), "SPY": series(400, 0.01), "VIX": series(15, 0.03), "VIX3M": series(17, 0.02)}
    monkeypatch.setattr(rl.truth_db, "daily", lambda db, k, *, end: {day: row for day, row in data[k].items() if day <= end})
    before = {r["date"]: r for r in rl.build_rows(None)}
    target = days[-50]
    for k in data:
        for x in days[days.index(target):]:
            data[k][x] = dict(data[k][x], open=data[k][x]["open"] * 1.2, high=data[k][x]["high"] * 1.3,
                              low=data[k][x]["low"] * 1.1, close=data[k][x]["close"] * 1.25)
    after = {r["date"]: r for r in rl.build_rows(None)}
    feature_names = list(rl.INDICATORS) + ["em", "legacy"]
    assert {n: before[target][n] for n in feature_names} == {n: after[target][n] for n in feature_names}
    assert before[target]["close_sigma"] != after[target]["close_sigma"]  # outcomes do change
