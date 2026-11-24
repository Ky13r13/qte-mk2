"""Two deliberately constructed sessions for inspecting S7 mechanics."""
from .volatility import MINUTE_NS
from .s7_study import schedule_for


def fixture(symbol):
    labels = ("2026-11-19", "2026-11-20")
    schedule = schedule_for(labels)
    index, volume, options = {}, {}, {}
    spot = 7000 if symbol == "SPX" else 700
    root = ("SPXW" if symbol == "SPX" else "XSP").ljust(6)
    for day in schedule.days:
        if day.open_ns is None or day.trading_date > labels[-1]:
            continue
        label = day.trading_date
        volume[label] = {s: 1000 for s in range(5, 391, 5)}
        if label not in labels:
            continue
        call = label == labels[0]
        side = "C" if call else "P"
        price = spot + (2 if call else -2)
        index[label] = {s: (spot+1, spot-1, spot) if s <= 30 else (price, price, price) for s in range(5, 391, 5)}
        if not call:
            index[label][40] = (spot, spot, spot)
        volume[label][35] = 3000
        key = root + label.replace("-", "")[2:]

        def book(right, strike, bid, ask, delta):
            return dict(key=f"{key}{right}{strike*1000:08d}", delta=delta, bid=bid, ask=ask, bid_size=20, ask_size=20)
        snapshots = []

        def snap(seconds, books, kind="update"):
            ts = day.open_ns + seconds*1_000_000_000
            snapshots.append(dict(source_ns=ts, available_ns=ts, kind=kind, books=books))
        snap(1800, [book("C", spot, 3, 3.1, .50), book("P", spot, 3, 3.1, -.50)], "chain")
        strike = spot + (10 if call else -10)
        delta = .12 if call else -.12
        snap(2100, [book(side, strike, .19, .20, delta)], "chain")
        snap(2131, [book(side, strike, .19, .20, delta)])
        for minute in range(36, 387):
            bid = (.60 if minute == 37 else 1.20 if minute >= 38 else .19) if call else .10
            snap(minute*60, [book(side, strike, bid, round(bid+.01, 2), delta)])
        ts = day.open_ns + 30*MINUTE_NS
        options[label] = dict(snapshots=snapshots, settlement=None, regimes=[
            dict(source_ns=ts, available_ns=ts, gamma="negative" if call else None,
                 vix=None if call else 22, vix3m=None if call else 20)])
    return index, volume, schedule, options
