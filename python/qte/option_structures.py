"""SPX-style European cash-settled option research structures, not broker orders.

Recipes implement the payoff ideas in 151 Trading Strategies, chapter 2.
Entry/exit timing is a separate strategy hypothesis. No American assignment,
futures delivery, margin model or executable-price claim is implied.
"""
from dataclasses import dataclass
import math

from .gamma_surface import calculate_gamma_surface


# Strike indices refer to strictly increasing caller-supplied strikes.
RECIPES = {
    "bull_call_spread": (("call", 0, 1), ("call", 1, -1)),
    "bear_put_spread": (("put", 0, -1), ("put", 1, 1)),
    "bull_put_spread": (("put", 0, 1), ("put", 1, -1)),
    "bear_call_spread": (("call", 0, -1), ("call", 1, 1)),
    "long_straddle": (("call", 0, 1), ("put", 0, 1)),
    "long_strangle": (("put", 0, 1), ("call", 1, 1)),
    "long_call_butterfly": (("call", 0, 1), ("call", 1, -2), ("call", 2, 1)),
    "long_put_butterfly": (("put", 0, 1), ("put", 1, -2), ("put", 2, 1)),
    "credit_iron_butterfly": (("put", 0, 1), ("put", 1, -1), ("call", 1, -1), ("call", 2, 1)),
    "credit_iron_condor": (("put", 0, 1), ("put", 1, -1), ("call", 2, -1), ("call", 3, 1)),
}


def _number(value, name, minimum=0):
    if type(value) not in (int, float) or not minimum <= value <= 1e9:
        raise ValueError(f"{name} must be a finite number in [{minimum}, 1e9]")
    return float(value)


@dataclass(frozen=True)
class OptionLeg:
    right: str
    strike: float
    quantity: int


@dataclass(frozen=True)
class SPXStructure:
    name: str
    strikes: tuple[float, ...]
    expiry_ns: int
    settlement: str = "pm_cash"
    multiplier: int = 100

    def __post_init__(self):
        if self.name not in RECIPES:
            raise ValueError("unsupported option structure")
        strikes = tuple(_number(k, "strike", 0.000001) for k in self.strikes)
        count = max(i for _, i, _ in RECIPES[self.name]) + 1
        if len(strikes) != count or any(a >= b for a, b in zip(strikes, strikes[1:])):
            raise ValueError("supply the exact number of strictly ascending strikes")
        if type(self.expiry_ns) is not int or not 0 <= self.expiry_ns < 2**63:
            raise ValueError("explicit UTC nanosecond expiry required")
        if self.settlement not in ("am_cash", "pm_cash") or self.multiplier != 100 or type(self.multiplier) is not int:
            raise ValueError("SPX research requires declared AM/PM cash settlement and multiplier 100")
        object.__setattr__(self, "strikes", strikes)

    @property
    def legs(self):
        return tuple(OptionLeg(right, self.strikes[index], quantity)
                     for right, index, quantity in RECIPES[self.name])

    def intrinsic_points(self, settlement_value):
        value = _number(settlement_value, "settlement value")
        return math.fsum(leg.quantity * max(0.0, (value - leg.strike) * (1 if leg.right == "call" else -1))
                         for leg in self.legs)

    def expiry_pnl(self, settlement_value, *, net_debit_points, fees=0):
        debit = _number(net_debit_points, "signed net debit", -1e9)
        fees = _number(fees, "fees")
        return self.multiplier * (self.intrinsic_points(settlement_value) - debit) - fees

    def risk(self, *, net_debit_points, fees=0):
        """Exact piecewise-linear extrema over nonnegative settlement values.

        Negative net debit means a credit. Fees are total dollars for one
        structure, including all constituent contracts. No margin inference.
        """
        values = [self.expiry_pnl(x, net_debit_points=net_debit_points, fees=fees)
                  for x in (0, *self.strikes)]
        slope = sum(leg.quantity for leg in self.legs if leg.right == "call")
        return {"maximum_loss": None if slope < 0 else max(0.0, -min(values)),
                "maximum_profit": None if slope > 0 else max(0.0, max(values)),
                "loss_unbounded": slope < 0, "profit_unbounded": slope > 0,
                "basis": "expiry payoff only; excludes early liquidation, financing and settlement uncertainty"}

    def gamma_scenario(self, *, at_ns, forward, volatility_pct, rate_pct=0):
        """Aggregate existing Black-76 surfaces using one declared forward and IV.

        SPX input is the index forward, not spot silently relabeled as a future.
        Common flat IV is a model scenario, not an observed smile or dealer GEX.
        """
        if type(at_ns) is not int or not 0 <= at_ns < self.expiry_ns:
            raise ValueError("scenario time must precede expiry")
        days = (self.expiry_ns - at_ns) / 86_400_000_000_000
        surfaces = [calculate_gamma_surface({"schema_version": 1, "forward": forward,
            "strike": leg.strike, "volatility_pct": volatility_pct, "days": days,
            "rate_pct": rate_pct, "multiplier": self.multiplier, "quantity": leg.quantity,
            "option_type": leg.right, "span_pct": 5}) for leg in self.legs]
        axes = surfaces[0]["surface"]
        grid = [[math.fsum(s["surface"]["gamma"][i][j] * leg.quantity * self.multiplier
                          for s, leg in zip(surfaces, self.legs))
                 for j in range(len(axes["forwards"]))] for i in range(len(axes["days"]))]
        return {"mode": "model_only", "name": self.name,
                "price_points": math.fsum(s["point"]["price"] * leg.quantity for s, leg in zip(surfaces, self.legs)),
                "position_delta": math.fsum(s["point"]["delta"] * leg.quantity * self.multiplier for s, leg in zip(surfaces, self.legs)),
                "position_gamma": math.fsum(s["point"]["position_gamma"] for s in surfaces),
                "surface": {"forwards": axes["forwards"], "days": axes["days"], "position_gamma": grid},
                "assumptions": "European cash settlement; supplied forward; common flat IV; calendar/365; not market quotes or signed dealer inventory"}
