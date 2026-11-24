"""Reference research strategies built only on the public QTE strategy API."""

from .moving_average import MovingAverageConfig, MovingAverageRegimeStrategy
from .candidates import CandidateConfig, CandidateStrategy
from .router import MacroRouterStrategy, RouterConfig
from .volume import VolumeConfig, VolumeStrategy
from .book151 import BookConfig, BookStrategy, futures_weights

__all__ = ["MovingAverageConfig", "MovingAverageRegimeStrategy", "CandidateConfig", "CandidateStrategy",
           "MacroRouterStrategy", "RouterConfig", "VolumeConfig", "VolumeStrategy",
           "BookConfig", "BookStrategy", "futures_weights"]
