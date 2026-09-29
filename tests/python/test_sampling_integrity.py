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
