"""Seeded adversarial market fixtures, never evidence of economic profitability.

The hourly and 24-hour clocks are continuous synthetic clocks, not exchange
calendars. Prices, volumes, regimes and macro context are all fabricated.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import random

from qte import Bar, Dataset


HOUR_NS = 3_600_000_000_000
DAY_NS = 24 * HOUR_NS
REGIMES = (
    "low_vol_trend", "high_vol_trend", "low_vol_range", "high_vol_range",
    "contraction_breakout", "selloff", "rebound", "gap_shock", "regime_switch",
)
GENERATOR_VERSION = "synthetic-regimes-v1"


@dataclass(frozen=True)
class SyntheticConfig:
    regime: str
    seed: int
    bars: int = 512
    interval_ns: int = HOUR_NS
    start_ns: int = 0
    symbol: str = "QTE_SYNTH"
    initial_price: float = 100.0

    def __post_init__(self) -> None:
        if self.regime not in REGIMES:
            raise ValueError("unknown synthetic regime")
        if type(self.seed) is not int or not 0 <= self.seed < 2**63:
            raise ValueError("synthetic seed must be an unsigned 63-bit integer")
        if type(self.bars) is not int or not 2 <= self.bars <= 100_000:
            raise ValueError("synthetic bars must be an integer in [2, 100000]")
        if type(self.interval_ns) is not int or self.interval_ns not in (HOUR_NS, DAY_NS):
            raise ValueError("synthetic clock must be one hour or 24 hours")
        if type(self.start_ns) is not int or not 0 <= self.start_ns < 2**63:
            raise ValueError("synthetic start must be a non-negative int64 timestamp")
        if self.start_ns + 2 * self.bars * self.interval_ns >= 2**63:
            raise ValueError("synthetic timestamps could overflow int64")
        if not isinstance(self.symbol, str) or not self.symbol or any(c.isspace() or ord(c) < 32 for c in self.symbol):
            raise ValueError("synthetic symbol must be nonempty and normalized")
        if type(self.initial_price) not in (int, float) or not math.isfinite(self.initial_price) or self.initial_price < 1:
            raise ValueError("synthetic initial price must be finite and at least one")


@dataclass(frozen=True)
class SyntheticCase:
    config: SyntheticConfig
    bars: tuple[Bar, ...]
    data: Dataset
    source_sha256: str


def regime_at(regime: str, index: int) -> str:
    """Generator-only state; strategies must never receive these labels as prices."""
    if regime != "regime_switch":
        return regime
    phases = ("low_vol_trend", "low_vol_range", "high_vol_trend", "selloff", "rebound", "high_vol_range")
    return phases[(index // 64) % len(phases)]


def generate_case(config: SyntheticConfig) -> SyntheticCase:
    """Create positive, valid, unadjusted/action-free invented OHLCV bars.

    Independent seed per window; no latent state enters the strategy interface.
    The range process mean-reverts to the initial price. The trend process has
    fixed drift. Stress processes intentionally inject adverse discontinuities.
    Noise is independent of strategy parameters. No calibration/optimization.
    """
    rng = random.Random(config.seed)
    previous = float(config.initial_price)
    clock = config.start_ns
    bars = []
    digest = hashlib.sha256()
    config_json = json.dumps(asdict(config), sort_keys=True, separators=(",", ":"), allow_nan=False)
    digest.update((GENERATOR_VERSION + config_json).encode())
    for index in range(config.bars):
        regime = regime_at(config.regime, index)
        noise = rng.gauss(0.0, 1.0)
        sigma, drift, gap = 0.002, 0.0, 0.0
        if regime == "low_vol_trend":
            drift = 0.0008
        elif regime == "high_vol_trend":
            sigma, drift = 0.016, 0.001
        elif regime in ("low_vol_range", "high_vol_range"):
            sigma = 0.004 if regime == "low_vol_range" else 0.023
            drift = 0.12 * math.log(config.initial_price / previous)
        elif regime == "contraction_breakout":
            phase = index % 100
            sigma = 0.0005 if phase < 70 else 0.012
            drift = 0.004 if 70 <= phase < 85 else -0.001 if phase >= 85 else 0.0
        elif regime == "selloff":
            sigma, drift = 0.019, -0.002
            gap = -0.07 if index % 71 == 35 else 0.0
        elif regime == "rebound":
            sigma = 0.018
            phase = index % 80
            drift = -0.025 if 25 <= phase < 30 else 0.012 if 30 <= phase < 40 else 0.0
        elif regime == "gap_shock":
            sigma = 0.012
            gap = (-0.12 if (index // 53) % 2 == 0 else 0.10) if index % 53 == 26 else 0.0
            if index and index % 79 == 0:
                clock += config.interval_ns  # Missing observation: no fabricated bar.
        opening = max(0.05, previous * math.exp(gap + rng.gauss(0, sigma * 0.15)))
        close = max(0.05, opening * math.exp(max(-0.25, min(0.25, drift + sigma * noise))))
        excursion = abs(rng.gauss(0, sigma * 0.4)) + 0.0001
        high = max(opening, close) * (1 + excursion)
        low = max(0.01, min(opening, close) / (1 + excursion))
        volume = 0.0 if index % 97 == 96 else float(rng.randint(50_000, 2_000_000))
        bar = Bar(config.symbol, clock, clock + config.interval_ns, opening, high, low, close, volume)
        bars.append(bar)
        digest.update(json.dumps([bar.start_ns, bar.end_ns, opening, high, low, close, volume],
                                 separators=(",", ":"), allow_nan=False).encode() + b"\n")
        previous = close
        clock += config.interval_ns
    source_hash = digest.hexdigest()
    dataset = Dataset.from_bars(bars, interval_ns=config.interval_ns,
                               source_id=f"synthetic:{GENERATOR_VERSION}:{source_hash}")
    return SyntheticCase(config, tuple(bars), dataset, source_hash)
