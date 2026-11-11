"""Offline VX expected-move scenario; no fetching, trading, or auto-roll."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path

from qte.adapters.cboe_vix import parse_cboe_vx_settlements
from qte.adapters.common import SourceProvenance
from qte.volatility import (
    CandleSnapshot,
    DAY_NS,
    GammaSensitivity,
    ProxyAssumption,
    VolatilityFutureObservation,
    analyze_vix_proxy,
)


_SYNTHETIC_NOW_NS = 1_799_107_200_000_000_000  # 2027-01-05T00:00:00Z
_CBOE_URL = (
    "https://www-api.cboe.com/us/futures/market_statistics/settlement/csv/?dt="
)


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive finite number") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def _finite_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a finite number") from None
    if not math.isfinite(parsed):
        raise argparse.ArgumentTypeError("must be a finite number")
    return parsed


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--vx-csv", type=Path)
    value.add_argument("--settlement-date")
    value.add_argument("--retrieved-at-ns", type=int)
    value.add_argument("--contract-id")
    value.add_argument("--volatility-scale", type=_positive_float, default=1.0,
                       help="explicit VX-to-demo-target scenario multiplier")
    value.add_argument("--gamma-dollars-per-point-squared", type=_finite_float,
                       default=0.02,
                       help="illustrative signed synthetic gamma sensitivity")
    return value


def _candle(label: str, end_ns: int, duration_ns: int, values) -> CandleSnapshot:
    open_, high, low, close = values
    return CandleSnapshot(
        label, "NQ-demo", end_ns - duration_ns, end_ns, end_ns,
        open_, high, low, close, "synthetic:nq-demo-ohlc-v1",
    )


def _synthetic_vx(now_ns: int):
    observation = VolatilityFutureObservation(
        "VX-synthetic@2027-02-01", now_ns + 27 * DAY_NS, 20.0,
        now_ns - DAY_NS, now_ns, "synthetic:vx-curve-v1", "synthetic-vintage-v1",
        evidence="synthetic", discretionary=None,
    )
    raw_identity = b"synthetic:vx-curve-v1:20.0:2027-02-01"
    provenance = SourceProvenance(
        "synthetic", "qte-vx-synthetic-example-v1", "1",
        hashlib.sha256(raw_identity).hexdigest(),
        ("evidence=synthetic", "availability=declared_fixture_time",
         "not_market_data=true"),
    )
    return (observation,), provenance, observation.contract_id


def build_output(arguments: argparse.Namespace) -> dict:
    saved_values = (
        arguments.vx_csv, arguments.settlement_date,
        arguments.retrieved_at_ns, arguments.contract_id,
    )
    if any(value is not None for value in saved_values) and not all(
            value is not None for value in saved_values):
        raise ValueError(
            "--vx-csv, --settlement-date, --retrieved-at-ns and --contract-id "
            "must be supplied together")

    if all(value is not None for value in saved_values):
        snapshot = parse_cboe_vx_settlements(
            arguments.vx_csv.read_bytes(),
            settlement_date=arguments.settlement_date,
            retrieved_at_ns=arguments.retrieved_at_ns,
            source_url=_CBOE_URL + arguments.settlement_date,
        )
        observations = snapshot.observations
        provenance = snapshot.provenance
        contract_id = arguments.contract_id
        now_ns = arguments.retrieved_at_ns
        kind = "saved_cboe_vx_mixed_with_synthetic_target"
    else:
        now_ns = _SYNTHETIC_NOW_NS
        observations, provenance, contract_id = _synthetic_vx(now_ns)
        kind = "synthetic_only"

    hour = _candle("1h", now_ns, 3_600_000_000_000,
                   (20000.0, 20120.0, 19920.0, 20040.0))
    daily = _candle("1d", now_ns, DAY_NS,
                    (19800.0, 20200.0, 19650.0, 20040.0))
    proxy = ProxyAssumption(
        "NQ-demo", arguments.volatility_scale, "explicit-example-scale-v1",
        now_ns, now_ns,
    )
    gamma = GammaSensitivity(
        "NQ-demo", arguments.gamma_dollars_per_point_squared,
        now_ns, now_ns, "synthetic:illustrative-dollar-gamma-v1",
    )
    analysis = analyze_vix_proxy(
        observations,
        timestamp_ns=now_ns,
        contract_id=contract_id,
        anchor=hour,
        proxy=proxy,
        candles=(hour, daily),
        gamma=gamma,
    )
    return {
        "kind": kind,
        "input_provenance": asdict(provenance),
        "target_input_kind": "synthetic_nq_demo_completed_ohlc",
        "analysis": asdict(analysis),
    }


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    arguments = cli.parse_args(argv)
    try:
        output = build_output(arguments)
    except (OSError, ValueError) as error:
        cli.error(str(error))
    print(json.dumps(output, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
