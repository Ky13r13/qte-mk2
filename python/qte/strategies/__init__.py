"""Reference research strategies built only on the public QTE strategy API."""

from .moving_average import MovingAverageConfig, MovingAverageRegimeStrategy
from .candidates import CandidateConfig, CandidateStrategy
from .router import MacroRouterStrategy, RouterConfig

__all__ = ["MovingAverageConfig", "MovingAverageRegimeStrategy", "CandidateConfig", "CandidateStrategy",
           "MacroRouterStrategy", "RouterConfig"]
