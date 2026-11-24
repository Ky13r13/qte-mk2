"""Cross-check QTE's volume-weighted indicators against independent textbook formulas."""
import random
from types import SimpleNamespace

import pytest

from qte.strategies.candidates import _atr
from qte.strategies.volume import _cmf, _mfi, _weighted_price


def bars(n=300, seed=3):
    rng, price, out = random.Random(seed), 100.0, []
    for _ in range(n):
        o = price
        price = max(1.0, price * (1 + rng.gauss(0, 0.01)))
        h, l = max(o, price) * (1 + rng.random() * 0.005), min(o, price) * (1 - rng.random() * 0.005)
        out.append(SimpleNamespace(open=o, high=h, low=l, close=price, volume=rng.randint(1_000, 50_000)))
    return out


def ref_vwma(window):
    return sum(b.close * b.volume for b in window) / sum(b.volume for b in window)


def ref_cmf(window):
    mfv = sum(((b.close - b.low) - (b.high - b.close)) / (b.high - b.low) * b.volume
              for b in window if b.high > b.low)
    return mfv / sum(b.volume for b in window)


def ref_mfi(window):
    tp = [(b.high + b.low + b.close) / 3 for b in window]
    pos = sum(tp[i] * window[i].volume for i in range(1, len(window)) if tp[i] > tp[i - 1])
    neg = sum(tp[i] * window[i].volume for i in range(1, len(window)) if tp[i] < tp[i - 1])
    return 100 - 100 / (1 + pos / neg)


def ref_atr(series, period):
    trs = [max(series[i].high - series[i].low, abs(series[i].high - series[i - 1].close),
               abs(series[i].low - series[i - 1].close)) for i in range(len(series) - period, len(series))]
    return sum(trs) / period


@pytest.mark.parametrize("seed", range(5))
def test_vwma_cmf_mfi_atr_match_textbook_formulas(seed):
    data = bars(seed=seed)
    for end in range(60, len(data), 7):
        w20, w15 = data[end - 20:end], data[end - 15:end]
        assert _weighted_price(w20) == pytest.approx(ref_vwma(w20), rel=1e-12)
        assert _cmf(w20) == pytest.approx(ref_cmf(w20), rel=1e-9, abs=1e-12)
        assert _mfi(w15) == pytest.approx(ref_mfi(w15), rel=1e-9)
        assert _atr(data[:end], 14) == pytest.approx(ref_atr(data[:end], 14), rel=1e-12)


def test_indicator_ranges_and_signs():
    up = [SimpleNamespace(open=10 + i, high=10.5 + i, low=9.5 + i, close=10.4 + i, volume=100) for i in range(20)]
    assert 0 < _cmf(up) <= 1 and _mfi(up[:15]) == 100.0
    down = [SimpleNamespace(open=30 - i, high=30.5 - i, low=29.5 - i, close=29.6 - i, volume=100) for i in range(20)]
    assert -1 <= _cmf(down) < 0 and _mfi(down[:15]) == 0.0
