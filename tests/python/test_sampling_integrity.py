import json
import pytest
import qte
from qte.research import compare_moving_average
from qte.strategies import MovingAverageConfig
from qte.adapters import load_alpaca_fixture
from test_adapters import ALPACA, HOUR_NS
from test_bindings import RoundTrip, dataset, engine

def test_real_run_sampling_enables_annualization():
    r=engine().run(dataset(), RoundTrip())
    config=qte.SamplingConfig([i*HOUR_NS for i in range(4)])
    points=qte.sample_equity(r, config)
    assert len(points)==4
    report=qte.analyze(r,qte.AnnualizationConfig(8766),config)
    assert report.returns == pytest.approx([0, .05, 0])
    assert report.annualized_volatility.value is not None
    assert report.sharpe_ratio.value is not None
    assert report.maximum_drawdown.value == qte.analyze(r).maximum_drawdown.value

def test_sampling_gaps_staleness_and_declared_session_grid():
    r=engine().run(dataset(), RoundTrip())
    with pytest.raises(ValueError,match='staleness'):
        qte.sample_equity(r,qte.SamplingConfig([0,HOUR_NS//2,3*HOUR_NS]))
    points=qte.sample_equity(r,qte.SamplingConfig([0,HOUR_NS//2,3*HOUR_NS],HOUR_NS))
    assert points[1].equity==1000
    # Explicit nonuniform observation times represent selected session boundaries.
    report=qte.analyze(r,qte.AnnualizationConfig(252),qte.SamplingConfig([0,HOUR_NS,3*HOUR_NS]))
    assert report.annualized_volatility.value is not None
    with pytest.raises(ValueError,match='strictly'):
        qte.sample_equity(r,qte.SamplingConfig([0,0,3*HOUR_NS]))
    with pytest.raises(ValueError,match='start and end'):
        qte.sample_equity(r,qte.SamplingConfig([HOUR_NS,3*HOUR_NS]))

def test_overlapping_split_is_rejected():
    with pytest.raises(ValueError,match='chronological'):
        compare_moving_average(in_sample=dataset(),out_of_sample=dataset(),symbol='SPY',
                               strategy_config=MovingAverageConfig(1,2,1))

@pytest.mark.parametrize('change', ['timeframe','token','feed'])
def test_adapter_rejects_metadata_mismatch(tmp_path,change):
    payload=json.loads(ALPACA.read_text())
    if change=='timeframe': payload['_qte']['timeframe']='1Min'
    if change=='token': del payload['next_page_token']
    if change=='feed': payload['_qte']['feed']='unknown'
    path=tmp_path/'bad.json'; path.write_text(json.dumps(payload))
    with pytest.raises(ValueError): load_alpaca_fixture(path,interval_ns=HOUR_NS)


def test_declared_session_close_grid_samples_actual_fixed_interval_replay():
    """Grid compatibility only: two final-half-hour bars, not session aggregation."""
    import hashlib
    from qte.adapters.common import parse_rfc3339_utc_ns as ns
    from qte.session_calendar import SessionDay, SessionSchedule

    schedule = SessionSchedule(
        "2024-11-27", "2024-11-29", "synthetic-sampling-fixture",
        hashlib.sha256(b"synthetic-sampling-fixture-v1").hexdigest(), "synthetic",
        [SessionDay("2024-11-27", ns("2024-11-27T14:30:00Z"), ns("2024-11-27T21:00:00Z")),
         SessionDay("2024-11-28"),
         SessionDay("2024-11-29", ns("2024-11-29T14:30:00Z"), ns("2024-11-29T18:00:00Z"))],
    )
    first_close, last_close = schedule.session_close_grid()
    half_hour = HOUR_NS // 2
    bars = [
        qte.Bar("SPY", first_close-half_hour, first_close, 100, 101, 99, 100, 1000),
        qte.Bar("SPY", last_close-half_hour, last_close, 110, 121, 109, 120, 1000),
    ]
    data = qte.Dataset.from_bars(bars, interval_ns=half_hour, source_id="synthetic-session-grid")

    class BuyOnce(qte.Strategy):
        def __init__(self):
            super().__init__()
            self.submitted = False

        def on_bar(self, ctx, bar):
            if not self.submitted:
                ctx.submit_order(qte.market_order("SPY", qte.OrderSide.BUY, 1))
                self.submitted = True

    result = qte.BacktestEngine(qte.BacktestConfig(1000)).run(data, BuyOnce())
    assert data.gap_count == 1
    assert len(result.fills) == 1
    assert result.fills[0].effective_ns == last_close-half_hour
    grid = (result.equity_curve[0].timestamp_ns, *schedule.session_close_grid())
    sampled = qte.sample_equity(result, qte.SamplingConfig(grid))
    assert [point.timestamp_ns for point in sampled] == list(grid)
    assert [point.equity for point in sampled] == [1000, 1000, 1010]
    # A calendar does not bypass the analytics requirement for the run anchor.
    with pytest.raises(ValueError, match="start and end"):
        qte.sample_equity(result, qte.SamplingConfig(schedule.session_close_grid()))
