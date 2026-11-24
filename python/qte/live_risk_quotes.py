"""Join normalized quote-recorder events into one as-of option-risk snapshot."""
from .forward_sessions import MAX_LINE
from .json_input import strict_json
from .live_risk import _MAX_NS, _integer


def read_events(stream):
    """Yield complete, size-limited records from an opened binary journal."""
    while line := stream.readline(MAX_LINE + 2):
        if not line.endswith(b"\n") or len(line) > MAX_LINE + 1:
            raise ValueError("partial or oversized normalized journal record")
        yield strict_json(line)


def apply_recorded_market(payload, events):
    """Replace all supplied marks, honoring availability, epoch boundaries and invalidations."""
    if type(payload) is not dict or type(payload.get("positions")) is not list:
        raise ValueError("risk input requires a positions array")
    asof = _integer(payload.get("asof_ns"), 1, _MAX_NS, "asof_ns")
    if payload.get("underlying") != "SPX":
        raise ValueError("this recorder carries the SPX index underlying")
    contracts = set()
    for leg in payload["positions"]:
        if type(leg) is not dict or type(leg.get("contract")) is not str:
            raise ValueError("risk positions require contract keys")
        contracts.add(leg["contract"])
    epoch, active, previous, spot, quotes = 0, False, 0, None, {}
    for row in events:
        if type(row) is not dict:
            raise ValueError("expected recorder event object")
        stamp = _integer(row.get("received_utc_ns"), 1, _MAX_NS, "received_utc_ns")
        if stamp < previous:
            raise ValueError("recorder receipt time regressed")
        previous = stamp
        if stamp > asof:
            break
        event = row.get("event")
        current = _integer(row.get("epoch"), 1, _MAX_NS, "epoch")
        if event == "subscribed":
            if current <= epoch:
                raise ValueError("subscription epoch must advance")
            epoch, active, spot, quotes = current, True, None, {}
            continue
        if not active or current != epoch:
            raise ValueError("event outside an acknowledged subscription epoch")
        if event == "disconnect":
            active, spot, quotes = False, None, {}
        elif event in ("underlying", "underlying_unavailable"):
            if row.get("symbol") != "$SPX":
                raise ValueError("expected the $SPX underlying")
            spot = None
            if event == "underlying":
                clock = _integer(row.get("source_trade_ms"), 1, _MAX_NS // 1_000_000, "source_trade_ms")
                spot = dict(price=row.get("price"), received_ns=stamp, source_ns=clock * 1_000_000)
        elif event in ("quote", "unavailable"):
            key = row.get("contract")
            if type(key) is not str:
                raise ValueError("contract key required")
            if key in contracts:
                quotes.pop(key, None)
                if event == "quote":
                    clock = _integer(row.get("source_quote_ms"), 1, _MAX_NS // 1_000_000, "source_quote_ms")
                    quotes[key] = dict(source_ns=clock * 1_000_000, received_ns=stamp,
                                       **{k: row.get(k) for k in ("bid", "ask", "bid_size", "ask_size")})
        else:
            raise ValueError("unknown recorder event")
    return dict(payload, spot=spot, positions=[dict(row, quote=quotes.get(row["contract"]))
                                               for row in payload["positions"]])


def _unmarked(settings, asof):
    from .live_risk import _fields
    _fields(settings, "schema underlying max_age_ms rate dividend_yield positions scenarios", "live settings")
    if settings["schema"] != "qte-live-risk-settings-v1" or settings["underlying"] != "SPX":
        raise ValueError("live risk requires SPXW holdings for the SPX recorder")
    if type(settings["positions"]) is not list:
        raise ValueError("expected an array of net positions")
    for leg in settings["positions"]:
        _fields(leg, "contract product quantity expiry_ns iv", "live position")
        if leg["product"] != "SPXW":
            raise ValueError("live risk supports the recorder's SPXW contracts")
    return dict({k: v for k, v in settings.items() if k != "schema"}, spot=None, asof_ns=asof,
                positions=[dict(leg, quote=None) for leg in settings["positions"]])


def live_settings(payload, now_ns):
    """Validate a static book declaration, retaining only live valuation assumptions."""
    from .live_risk import _fields, position_risk
    if type(payload) is dict and "schema" in payload:
        settings = payload
    else:
        _fields(payload, "asof_ns max_age_ms underlying spot rate dividend_yield positions scenarios", "risk")
        if type(payload["positions"]) is not list:
            raise ValueError("risk input requires a positions array")
        for leg in payload["positions"]:
            _fields(leg, "contract product quantity expiry_ns iv quote", "position")
        settings = {k: v for k, v in payload.items() if k not in {"asof_ns", "spot", "positions"}}
        settings.update(schema="qte-live-risk-settings-v1",
                        positions=[{k: v for k, v in leg.items() if k != "quote"} for leg in payload["positions"]])
    position_risk(_unmarked(settings, now_ns))
    return settings


def risk_from_status(settings, status, now_ns):
    """Revalue a declared book with the latest connected, same-epoch market snapshot."""
    from .live_risk import position_risk
    now_ns = _integer(now_ns, 1, _MAX_NS, "now_ns")
    payload = _unmarked(settings, now_ns)
    if (type(status) is not dict or status.get("schema") != "schwab_live_status_v1"
            or type(status.get("connected")) is not bool):
        raise ValueError("expected a Schwab live capture status")
    limit = _integer(status.get("max_age_ms"), 1, _MAX_NS // 1_000_000, "recorder max_age_ms")
    requested = _integer(settings["max_age_ms"], 1, 86_400_000, "risk max_age_ms")
    payload["max_age_ms"] = min(limit, requested)
    state = "disconnected"
    epoch = _integer(status.get("epoch"), 0, _MAX_NS, "epoch")
    if status["connected"]:
        _integer(epoch, 1, _MAX_NS, "connected epoch")
        receipt = _integer(status.get("received_utc_ns"), 1, now_ns, "status received_utc_ns")
        state = "stale" if now_ns - receipt > payload["max_age_ms"] * 1_000_000 else "connected"
        quotes, contracts = status.get("quotes"), status.get("contracts")
        if type(quotes) is not list or type(contracts) is not list or any(type(k) is not str for k in contracts):
            raise ValueError("expected recorder contracts and quotes arrays")
        seen = set()
        for row in quotes:
            if (type(row) is not dict or row.get("event") not in ("quote", "unavailable")
                    or type(row.get("contract")) is not str or row["contract"] not in contracts
                    or row["contract"] in seen):
                raise ValueError("unexpected or duplicate contract in recorder status")
            seen.add(row["contract"])
        rows = list(quotes)
        underlying = status.get("underlying")
        if underlying is not None:
            if type(underlying) is not dict or underlying.get("event") not in ("underlying", "underlying_unavailable"):
                raise ValueError("expected an index observation in recorder status")
            rows.append(underlying)
        for row in rows:
            _integer(row.get("received_utc_ns"), 1, receipt, "market row received_utc_ns")
        rows.sort(key=lambda row: row["received_utc_ns"])
        subscription = dict(event="subscribed", epoch=epoch, received_utc_ns=rows[0]["received_utc_ns"] if rows else receipt)
        payload = apply_recorded_market(payload, [subscription, *rows])
    report = position_risk(payload)
    report.update(market_input="live_recorder", capture_state=state, capture_epoch=epoch)
    return report
