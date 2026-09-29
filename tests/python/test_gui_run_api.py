"""Real engine export / HTTP parity; no browser-side financial calculation."""
import asyncio
import csv
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from qte.cli import run_research
from qte.gui import create_app

REPOSITORY = Path(__file__).parents[2]
BASE = 'http://127.0.0.1:8765'


def rows(path):
    with path.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))


def visit(repository, check):
    async def scenario():
        codes = []
        app = create_app(repository, code_sink=codes.append)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                login = await client.post('/api/v1/session', headers={'Origin': BASE},
                                          json={'schema_version': 1, 'code': codes[0]})
                assert login.status_code == 200
                headers = {'X-QTE-Token': login.json()['request_token'], 'Origin': BASE}
                result = await client.post('/api/v1/catalog/register', headers=headers,
                                           json={'schema_version': 1, 'path': 'build/reference'})
                assert result.status_code == 200, result.text
                await check(client, headers, result.json()['artifact']['id'])
    asyncio.run(scenario())


@pytest.mark.parametrize('config_name,sampled', [('research-run.json', True), ('trend-research-run.json', False)])
def test_run_views_match_actual_engine_exports(tmp_path, config_name, sampled):
    output = run_research(REPOSITORY / 'examples' / config_name, tmp_path / 'build/reference')
    report = json.loads((output / 'report.json').read_text())
    manifest = json.loads((output / 'manifest.json').read_text())

    async def check(client, headers, item_id):
        base = f'/api/v1/runs/{item_id}'
        assert (await client.get(base)).status_code == 403
        response = await client.get(base, headers=headers)
        assert response.status_code == 200, response.text
        assert response.headers['cache-control'] == 'no-store'
        detail = response.json()['run']
        for name, metric in report['metrics'].items():
            assert detail['metrics'][name]['value'] == metric['value']
            assert detail['metrics'][name]['undefined_reason'] == metric['undefined_reason']
        assert detail['provenance']['dataset_start_ns'] == str(manifest['dataset_start_ns'])
        assert int(detail['provenance']['dataset_start_ns']) > 2**53
        assert detail['counts']['fills'] == str(report['fill_count'])
        assert detail['capabilities']['positions'] == 'not_recorded'
        annual = detail['metrics']['annualized_return']
        if sampled:
            assert annual['value'] is not None
            assert annual['recorded_sampling_policy']['interval_ns'] == str(manifest['sampling']['interval_ns'])
            assert annual['recorded_sampling_policy']['timestamps_ns'] == [str(t) for t in manifest['sampling']['timestamps_ns']]
        else:
            assert annual['value'] is None and annual['undefined_reason']

        for mode, filename in [('event', 'equity.csv'), ('sampled', 'sampled_equity.csv')]:
            series_response = await client.get(f'{base}/series?sampling={mode}', headers=headers)
            if mode == 'sampled' and not sampled:
                assert series_response.status_code == 404
                continue
            assert series_response.status_code == 200, series_response.text
            series = series_response.json()['series']
            source = rows(output / filename)
            assert series['raw_count'] == series['display_count'] == str(len(source))
            assert series['sequence_status'] == 'not_recorded'
            assert [p['timestamp_ns'] for p in series['points']] == [p['timestamp_ns'] for p in source]
            assert [p['equity'] for p in series['points']] == [float(p['equity']) for p in source]
            assert [p['row_ordinal'] for p in series['points']] == [str(i) for i in range(len(source))]
            assert all(p['sequence'] is None for p in series['points'])

        for name in ('orders', 'fills', 'equity'):
            source = rows(output / f'{name}.csv')
            response = await client.get(f'{base}/tables/{name}?offset=1&limit=1', headers=headers)
            assert response.status_code == 200, response.text
            table = response.json()
            assert table['rows'] == source[1:2]
            assert table['total'] == len(source) and table['offset'] == table['limit'] == 1
            assert all(isinstance(v, str) for row in table['rows'] for v in row.values())

    visit(tmp_path, check)


def test_run_queries_reject_ambiguous_out_of_range_and_unknown_fields(tmp_path):
    run_research(REPOSITORY / 'examples/research-run.json', tmp_path / 'build/reference')

    async def check(client, headers, item_id):
        base = f'/api/v1/runs/{item_id}'
        for query in ('max_points=3', 'max_points=2001', 'max_points=true', 'start_ns=1.0',
                      'start_ns=01', 'start_ns=9223372036854775808', 'end_ns=-9223372036854775809',
                      'start_ns=2&end_ns=1', 'sampling=event&sampling=sampled', 'command=run'):
            assert (await client.get(f'{base}/series?{query}', headers=headers)).status_code == 400, query
        assert (await client.get(base + '?unknown=1', headers=headers)).status_code == 400
        assert (await client.get(base + '/tables/fills?limit=201', headers=headers)).status_code == 400
        assert (await client.get(base + '/tables/positions', headers=headers)).status_code == 404
        response = await client.get(base + '/series?start_ns=-9223372036854775808&end_ns=9223372036854775807', headers=headers)
        assert response.status_code == 200, response.text

    visit(tmp_path, check)


@pytest.mark.parametrize('protected_name,route', [('report.json', ''), ('equity.csv', '/series'), ('fills.csv', '/tables/fills')])
def test_run_routes_cannot_bypass_inherited_protection(tmp_path, protected_name, route):
    output = run_research(REPOSITORY / 'examples/research-run.json', tmp_path / 'build/reference')
    unknown = tmp_path / 'build/unknown'
    unknown.mkdir()
    (unknown / 'protected.bin').write_bytes((output / protected_name).read_bytes())
    from qte.gui.catalog import Catalog
    Catalog(tmp_path).register('build/unknown')

    async def check(client, headers, item_id):
        response = await client.get(f'/api/v1/runs/{item_id}{route}', headers=headers)
        assert response.status_code == 403, response.text
        assert response.json()['error']['code'] == 'holdout_protected'
        catalog = await client.get(f'/api/v1/artifacts/{item_id}', headers=headers)
        assert catalog.json()['artifact']['needs_disclosure'] is True
        assert not list((tmp_path / 'build/gui/disclosures').glob('revealed_outside_protocol-*.json'))

    visit(tmp_path, check)


def test_run_tampering_preserves_catalog_diagnostics(tmp_path):
    output = run_research(REPOSITORY / 'examples/research-run.json', tmp_path / 'build/reference')

    async def check(client, headers, item_id):
        (output / 'equity.csv').write_bytes(b'tampered fixture')
        assert (await client.get(f'/api/v1/runs/{item_id}', headers=headers)).status_code == 409
        response = await client.get(f'/api/v1/artifacts/{item_id}', headers=headers)
        assert response.status_code == 200
        artifact = response.json()['artifact']
        assert artifact['status']['integrity'] == 'invalid' and artifact['errors']

    visit(tmp_path, check)


def test_missing_metric_value_is_invalid_not_an_uncaught_view_error(tmp_path):
    output = run_research(REPOSITORY / 'examples/research-run.json', tmp_path / 'build/reference')
    report = json.loads((output / 'report.json').read_text())
    del report['metrics']['total_return']['value']
    (output / 'report.json').write_text(json.dumps(report))
    marker = json.loads((output / 'complete.json').read_text())
    marker['sha256']['report.json'] = hashlib.sha256((output / 'report.json').read_bytes()).hexdigest()
    (output / 'complete.json').write_text(json.dumps(marker))

    async def check(client, headers, item_id):
        response = await client.get(f'/api/v1/artifacts/{item_id}', headers=headers)
        assert response.status_code == 200
        assert response.json()['artifact']['status']['integrity'] == 'invalid'
        response = await client.get(f'/api/v1/runs/{item_id}', headers=headers)
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'run_view_unavailable'

    visit(tmp_path, check)
