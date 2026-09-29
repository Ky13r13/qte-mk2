"""Catalog HTTP authorization, exact wire values and engine-export parity."""
import asyncio
from pathlib import Path

import httpx

from qte.cli import run_research
from qte.gui import create_app

REPOSITORY = Path(__file__).parents[2]
BASE = 'http://127.0.0.1:8765'


def test_catalog_api_is_explicit_authenticated_bounded_and_exact(tmp_path):
    output = run_research(REPOSITORY / 'examples/trend-research-run.json', tmp_path / 'build/reference')

    async def scenario():
        codes = []
        app = create_app(tmp_path, code_sink=codes.append)
        database = tmp_path / '.cache/qte-gui/catalog.sqlite3'
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                assert (await client.get('/api/v1/artifacts')).status_code == 401
                assert not database.exists()
                login = await client.post('/api/v1/session', headers={'Origin': BASE},
                                          json={'schema_version': 1, 'code': codes[0]})
                headers = {'X-QTE-Token': login.json()['request_token']}
                initial = await client.get('/api/v1/artifacts', headers=headers)
                assert initial.json()['items'] == []
                assert not database.exists(), 'navigation must not create the database'
                body = {'schema_version': 1, 'path': 'build/reference'}
                assert (await client.post('/api/v1/catalog/register', headers=headers, json=body)).status_code == 403
                assert not database.exists()
                registered = await client.post('/api/v1/catalog/register', headers={**headers, 'Origin': BASE}, json=body)
                assert registered.status_code == 200, registered.text
                detail = registered.json()['artifact']
                assert detail['kind'] == 'single_run_v1'
                assert detail['status']['integrity'] == 'verified'
                assert detail['status']['evidence'] == 'unknown', 'CSV is not historical evidence by itself'
                assert detail['file_count'] == '6'
                assert 'files' not in detail
                item_id = detail['id']
                listing = await client.get('/api/v1/artifacts', headers=headers)
                assert listing.json()['total'] == 1
                assert listing.json()['items'][0]['id'] == item_id
                files = await client.get(f'/api/v1/artifacts/{item_id}/files?limit=2', headers=headers)
                assert len(files.json()['items']) == 2
                assert files.json()['total'] == 6
                assert all(isinstance(item['size'], str) for item in files.json()['items'])
                all_files = await client.get(f'/api/v1/artifacts/{item_id}/files', headers=headers)
                equity = next(item for item in all_files.json()['items'] if item['name'] == 'equity.csv')
                route = f'/api/v1/artifacts/{item_id}/files/{equity["id"]}/download'
                assert (await client.get(route)).status_code == 403
                download = await client.get(route, headers=headers)
                assert download.status_code == 200
                assert download.content == (output / 'equity.csv').read_bytes()
                assert download.headers['content-disposition'].startswith('attachment;')
                assert download.headers['content-type'] == 'application/octet-stream'
                assert download.headers['cache-control'] == 'no-store'
                marker = next(item for item in all_files.json()['items'] if item['name'] == 'complete.json')
                marker_download = await client.get(f'/api/v1/artifacts/{item_id}/files/{marker["id"]}/download', headers=headers)
                assert marker_download.status_code == 200
                assert marker_download.content == (output / 'complete.json').read_bytes()
                assert (await client.get('/api/v1/artifacts?limit=201', headers=headers)).status_code == 400
                assert (await client.get('/api/v1/artifacts?offset=1&offset=2', headers=headers)).status_code == 400
                for path in ('../', '/etc', '.git', 'build/reference/../reference'):
                    denied = await client.post('/api/v1/catalog/register', headers={**headers, 'Origin': BASE},
                                               json={'schema_version': 1, 'path': path})
                    assert denied.status_code in (400, 403)
    asyncio.run(scenario())


def test_tampered_or_disappeared_registered_root_remains_visible(tmp_path):
    output = run_research(REPOSITORY / 'examples/trend-research-run.json', tmp_path / 'build/reference')

    async def scenario():
        codes = []
        app = create_app(tmp_path, code_sink=codes.append)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                login = await client.post('/api/v1/session', headers={'Origin': BASE}, json={'schema_version': 1, 'code': codes[0]})
                headers = {'X-QTE-Token': login.json()['request_token'], 'Origin': BASE}
                registered = await client.post('/api/v1/catalog/register', headers=headers,
                                               json={'schema_version': 1, 'path': 'build/reference'})
                item_id = registered.json()['artifact']['id']
                (output / 'orders.csv').write_bytes(b'changed without updating completion')
                bad = await client.get(f'/api/v1/artifacts/{item_id}', headers=headers)
                assert bad.status_code == 200, bad.text
                assert bad.json()['artifact']['status']['integrity'] == 'invalid'
                output.rename(output.with_name('moved-reference'))
                listing = await client.get('/api/v1/artifacts', headers=headers)
                assert listing.status_code == 200, listing.text
                assert listing.json()['total'] == 1
                assert listing.json()['items'][0]['id'] == item_id
                assert listing.json()['items'][0]['status']['integrity'] != 'verified'
                assert listing.json()['items'][0]['errors']
    asyncio.run(scenario())


def test_unknown_attachment_requires_journaled_confirmation(tmp_path):
    root = tmp_path / 'build/unknown'
    root.mkdir(parents=True)
    (root / 'notes.txt').write_text('unclassified local fixture', encoding='utf-8')

    async def scenario():
        codes = []
        app = create_app(tmp_path, code_sink=codes.append)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                login = await client.post('/api/v1/session', headers={'Origin': BASE}, json={'schema_version': 1, 'code': codes[0]})
                headers = {'X-QTE-Token': login.json()['request_token'], 'Origin': BASE}
                registered = await client.post('/api/v1/catalog/register', headers=headers,
                                               json={'schema_version': 1, 'path': 'build/unknown'})
                assert registered.status_code == 200, registered.text
                item_id = registered.json()['artifact']['id']
                files = await client.get(f'/api/v1/artifacts/{item_id}/files', headers=headers)
                file_id = files.json()['items'][0]['id']
                route = f'/api/v1/artifacts/{item_id}/files/{file_id}/download'
                assert (await client.get(route, headers=headers)).status_code == 403
                reveal = f'/api/v1/artifacts/{item_id}/reveal-holdout'
                journal = tmp_path / 'build/gui/disclosures'
                before = {p.name: p.read_bytes() for p in journal.glob('*.json')}
                assert not list(journal.glob('revealed_outside_protocol-*.json'))
                assert (await client.post(reveal, headers=headers, json={'schema_version': 1, 'confirmation': True})).status_code == 400
                assert {p.name: p.read_bytes() for p in journal.glob('*.json')} == before
                response = await client.post(reveal, headers=headers, json={'schema_version': 1, 'confirmation': 'reveal outside protocol'})
                assert response.status_code == 200, response.text
                assert response.json()['artifact']['status']['disclosure'] == 'revealed_outside_protocol'
                assert list(journal.glob('revealed_outside_protocol-*.json'))
                assert (await client.get(route, headers=headers)).content == b'unclassified local fixture'
    asyncio.run(scenario())
