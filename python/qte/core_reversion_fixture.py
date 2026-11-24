"""Hand-built SPXW session for exercising each study through its public CLI."""
from .s7_study import schedule_for
from .volatility import MINUTE_NS as M


def fixture(strategy):
    label = "2026-11-20"
    schedule = schedule_for([label])
    day = schedule.day(label)
    s1 = strategy == "S1"
    bars, volume = {}, {}
    for n in range(1, 391):
        if s1:
            close = 6990 if 31 <= n <= 35 else 6991 if n == 36 else 7000
            bar = (close, close, close, close)
        else:
            close = max(7020, 7042-n*.4) if n <= 15 else 7020
            bar = (7042 if n == 1 else close, 7044 if n == 1 else close+.2, close-.2, close)
        start = day.open_ns+(n-1)*M
        bars[start], volume[start] = bar, 1000
    slot = 36 if s1 else 15
    at = day.open_ns+slot*M
    right, strikes, deltas = ("C", (6995, 7000), (.40, .25)) if s1 else ("P", (7035, 7020), (-.45, -.24))
    snapshots = []
    for seconds, prices, kind in [(0, (2.00, 1.00), "chain"), (31, (2.00, 1.00), "update"),
                                  (60, (2.50, 1.00), "update"), (61, (2.50, 1.00), "update"),
                                  (121, (2.50, 1.00), "update")]:
        books = [dict(key=f"SPXW  261120{right}{k*1000:08d}", delta=d, bid=p, ask=round(p+.05, 2),
                      bid_size=5, ask_size=5) for k, d, p in zip(strikes, deltas, prices)]
        stamp = at + seconds*10**9
        snapshots.append(dict(source_ns=stamp, available_ns=stamp, kind=kind, books=books))
    regime_at = day.open_ns+(slot//15)*15*M
    payload = dict(snapshots=snapshots, settlement=None, regimes=[
        dict(source_ns=regime_at, available_ns=regime_at, gamma="positive", vix=18, vix3m=20)],
        calendar=[dict(available_ns=day.open_ns, event_day=False, top10_catalyst=False, macro_times_ns=[])])
    return {label: bars}, {label: volume}, {"2026-11-19": 7000}, schedule, {label: payload}
