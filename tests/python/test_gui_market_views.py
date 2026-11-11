"""Market chart API is a safe, factual projection of lab evidence."""
from __future__ import annotations

import json
import shutil

from qte.gui.market_views import project_lab_market
from qte.gui.artifacts import ArtifactError, inspect_artifact
from qte.gui.catalog import Catalog
from qte.strategy_lab import run_lab


def test_lab_market_is_raw_and_refuses_test_window(tmp_path):
    config = tmp_path / 'input.json'
    config.write_text(json.dumps({'schema_version': 1, 'seed': 1, 'bars_per_window': 128,
                                  'timeframes': ['hourly'], 'regimes': ['low_vol_trend']}))
    artifact = inspect_artifact(run_lab(config, tmp_path / 'out'))
    market = project_lab_market(artifact, 'hourly', 'train-low_vol_trend')
    assert market['status'] == 'recorded'
    assert market['candlestick_policy'] == 'raw_no_aggregation'
    assert market['runs'][0]['signal_status'] == 'not_recorded'
    assert all(fill['signal_ns'] is None for fill in market['runs'][0].get('fills', []))
    assert all(candle['low'] <= candle['open'] <= candle['high'] for candle in market['candles'])
    compared = project_lab_market(artifact, 'hourly', 'train-low_vol_trend',
                                  candidate='trend-ungated', compare='trend-macro', cost='stress')
    assert [run['candidate'] for run in compared['runs']] == ['trend-ungated', 'trend-macro']
    assert compared['cost_scenario'] == 'stress'
    try:
        project_lab_market(artifact, 'hourly', 'test-low_vol_trend')
    except ArtifactError as exc:
        assert exc.code == 'holdout_protected'
    else:
        raise AssertionError('test window must remain withheld')


def test_lab_market_propagates_changed_run_and_honors_safe_file_protection(tmp_path):
    config = tmp_path / 'input.json'
    config.write_text(json.dumps({'schema_version': 1, 'seed': 2, 'bars_per_window': 128,
                                  'timeframes': ['hourly'], 'regimes': ['low_vol_trend']}))
    source = run_lab(config, tmp_path / 'source')
    repository = tmp_path / 'repo'; shutil.copytree(source, repository / 'build/lab')
    artifact = inspect_artifact(source)
    changed = source / 'hourly/runs/train-low_vol_trend--trend-ungated--base/fills.csv'
    changed.write_text(changed.read_text() + '\n')
    try:
        project_lab_market(artifact, 'hourly', 'train-low_vol_trend')
    except ArtifactError as exc:
        assert exc.code == 'artifact_changed'
    else:
        raise AssertionError('changed recorded run must not become missing')

    catalog = Catalog(repository)
    item = catalog.register('build/lab')
    verified = catalog.inspect(item['id'])
    assert catalog.market_view(item['id'], timeframe='hourly', window='train-low_vol_trend')['status'] == 'recorded'
    name = 'hourly/data/train-low_vol_trend.csv'
    identity = f"file-sha256:{verified.metadata['file_hashes'][name]}"
    catalog.disclosures.mark_protected([identity])
    try:
        catalog.market_view(item['id'], timeframe='hourly', window='train-low_vol_trend')
    except ArtifactError as exc:
        assert exc.code == 'holdout_protected'
    else:
        raise AssertionError('durably protected copied train data must be withheld')
    catalog.reveal(item['id'], 'reveal outside protocol')
    try:
        catalog.market_view(item['id'], timeframe='hourly', window='test-low_vol_trend')
    except ArtifactError as exc:
        assert exc.code == 'holdout_protected'
    else:
        raise AssertionError('revealing artifact files never authorizes structured test market data')
