"""Read-only session capture and translation at the Schwab boundary."""
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
from time import time_ns

from qte import truth_db
from qte.brokers.schwab_auth import TokenStore, market_client
from qte.brokers.schwab_capture import capture_read

MINUTE = 60_000_000_000


def capture(directory, *, clock=time_ns, chain_only=False):
    now = clock()
    session = datetime.fromtimestamp(now // 10**9, truth_db.NY).date()
    contract = json.loads(truth_db.DEFAULT_CONTRACT.read_bytes())
    store = TokenStore(Path.cwd())
    with store.lock():
        client = market_client(store.load(), contract)
    directory.mkdir(parents=True, exist_ok=False)

    def save(name, operation, **query):
        capture_read(client, operation, directory / (name + ".json"), path_parameters={},
                     query_parameters=query, clock=clock)

    common = dict(symbol="$SPX", contractType="ALL", includeUnderlyingQuote=True)
    if not chain_only:
        for sym in ("SPX", "VIX", "VIX3M"):
            save(sym.lower()+"-5m", "priceHistory", symbol="$"+sym, frequency=5,
                 startDate=(now-10*1440*MINUTE)//10**6, endDate=now//10**6, frequencyType="minute",
                 periodType="day", needExtendedHoursData=False)
        save("spx-chain-next10", "optionChain", **common, strikeCount=60,
             fromDate=(session+timedelta(days=1)).isoformat(), toDate=(session+timedelta(days=10)).isoformat())
    save("spx-chain-0dte", "optionChain", **common, strikeCount=160,
         fromDate=session.isoformat(), toDate=session.isoformat())


def read(directory, name, operation, symbol):
    record = json.loads((directory / (name+".json")).read_bytes())
    request = record["request"]
    if (record["provider"] != "schwab" or request["operation_id"] != operation or
            request["query_parameters"]["symbol"] != "$"+symbol or
            sha256(truth_db._canonical(record["response"])).hexdigest() != record["response_canonical_json_sha256"]):
        raise ValueError(f"Capture identity or checksum mismatch in {name}")
    if not 0 < record["request_started_utc_ns"] <= record["response_received_utc_ns"]:
        raise ValueError("Invalid capture clocks")
    return record


def bars(record, asof):
    params = record["request"]["query_parameters"]
    if params["frequency"] != 5 or params["frequencyType"] != "minute" or params["needExtendedHoursData"]:
        raise ValueError("Expected regular-session five-minute candles")
    result = {}
    for bar in record["response"]["candles"]:
        start = bar["datetime"] * 10**6
        if start + 5*MINUTE > min(asof, params["endDate"]*10**6):
            continue
        valid, reason = truth_db._check(bar)
        if not valid or start in result:
            raise ValueError(f"Invalid five-minute candle: {reason or 'duplicate'}")
        result[start] = dict(zip(("open", "high", "low", "close"), valid[:4]))
    return result


def chain_contracts(record, day):
    result = {}
    for field, right in (("callExpDateMap", "C"), ("putExpDateMap", "P")):
        for expiry, strikes in record["response"][field].items():
            date_label = expiry.split(":")[0]
            if not day <= date_label <= (date.fromisoformat(day)+timedelta(days=10)).isoformat():
                continue
            for strike, listings in strikes.items():
                for row in listings:
                    key = row["symbol"]
                    expected = f'SPXW  {date_label[2:].replace("-", "")}{right}{round(float(strike)*1000):08d}'
                    if (key != expected or row.get("settlementType") != "P"
                            or row.get("nonStandard") is not False or row.get("multiplier") != 100):
                        continue
                    if key in result:
                        raise ValueError("Duplicate SPXW contract")
                    result[key] = dict(row, expiry=date_label, right=right, strike=float(strike),
                                       expiry_ns=int(datetime.fromisoformat(row["expirationDate"]).timestamp()*10**9))
    return result
