"""The Book Strategy Explorer only serves registered local data after login."""
import asyncio
from hashlib import sha256
import json
from pathlib import Path

import httpx
import pytest

from qte.gui.app import create_app


def test_chart_export_checks_study_and_capture_identity_before_publication(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'examples'))
    import book151_gui_export as charts

    protocol = {'daily_hashes': dict.fromkeys(charts.SYMBOLS, 'daily-source'),
                'minute_hashes': dict.fromkeys(charts.SYMBOLS, 'minute-source')}
    hashes = {}
    for name, value in [('protocol.json', protocol), ('results.json', {}), ('passing.json', [])]:
        raw = json.dumps(value).encode()
        (tmp_path / name).write_bytes(raw)
        hashes[name] = sha256(raw).hexdigest()
    (tmp_path / 'manifest.json').write_text(json.dumps({'outputs': hashes}))
    output = tmp_path / 'published'
    monkeypatch.setattr(charts, 'read_daily', lambda path, symbol: ([], 'daily-source'))
    monkeypatch.setattr(charts, 'read_full_minute_sessions', lambda path, symbol: ({}, 'different-source'))
    with pytest.raises(ValueError, match='SPY minute capture differs'):
        charts.export(tmp_path, tmp_path, tmp_path, output)
    (tmp_path / 'passing.json').write_text('[1]')
    with pytest.raises(ValueError, match='checksum mismatch for passing.json'):
        charts.export(tmp_path, tmp_path, tmp_path, output)
    assert not output.exists()


def test_book151_route_auth_and_resource_allowlist(tmp_path):
    folder=tmp_path/'build'/'book151-chart'
    folder.mkdir(parents=True)
    (folder/'index.json').write_text('{"clocks":["weekly"]}')
    (folder/'run-weekly-10.4.json').write_text('{"later":{}}')
    (folder/'private.json').write_text('{"private":true}')
    app=create_app(tmp_path,code_sink=lambda _:None)
    origin='http://127.0.0.1:8765'

    async def verify():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=origin) as client:
            root='/api/v1/book151-study/'
            assert (await client.get(root+'index')).status_code==401
            login=await client.post('/api/v1/session',headers={'Origin':origin},json={
                'schema_version':1,'code':app.state.session_controller.access_code_for_testing})
            headers={'Origin':origin,'X-QTE-Token':login.json()['request_token']}
            index=await client.get(root+'index',headers=headers)
            assert index.status_code==200 and index.json()=={'clocks':['weekly']}
            assert (await client.get(root+'run-weekly-10.4',headers=headers)).status_code==200
            assert (await client.get(root+'private',headers=headers)).status_code==404
            assert (await client.get(root+'index?path=private',headers=headers)).status_code==400
            assert (await client.get('/research/book151')).status_code==200
    try:
        asyncio.run(verify())
    finally:
        app.state.research_runs.close()
