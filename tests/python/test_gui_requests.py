"""Boundary regression tests independent of stored artifacts or the engine."""
import asyncio

import httpx
import pytest
from starlette.requests import Request

from qte.gui import create_app
from qte.gui.dto import artifact_value
from qte.gui.requests import json_request, strict_json
from qte.gui.security import SecurityError


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e309}', b'{"x":-Infinity}'])
def test_strict_json_rejects_ambiguous_or_nonfinite(raw):
    with pytest.raises(ValueError):
        strict_json(raw)


def test_chunked_login_stops_at_size_limit_before_consuming_more():
    async def scenario():
        requests = 0
        async def receive():
            nonlocal requests
            requests += 1
            assert requests <= 2, 'reader consumed beyond its declared bound'
            return {'type': 'http.request', 'body': b'x' * 3000, 'more_body': True}
        request = Request({'type': 'http', 'headers': [(b'content-type', b'application/json')]}, receive)
        with pytest.raises(SecurityError) as error:
            await json_request(request, {'code'})
        assert error.value.status == 413
        assert requests == 2
    asyncio.run(scenario())


def test_exact_artifact_integer_conversion_retains_null_and_boolean():
    original = {'timestamp_ns': 9223372036854775807, 'sequence': 18446744073709551615,
                'quantity': -9223372036854775807, 'nested': [True, None, 1.25, {'seed': 2**60}]}
    wire = artifact_value(original)
    assert wire['timestamp_ns'] == '9223372036854775807'
    assert wire['sequence'] == '18446744073709551615'
    assert wire['quantity'] == '-9223372036854775807'
    assert wire['nested'] == [True, None, 1.25, {'seed': '1152921504606846976'}]
    assert isinstance(original['timestamp_ns'], int)


def test_overflow_login_and_duplicate_query_receive_bounded_errors(tmp_path):
    async def scenario():
        codes = []
        app = create_app(tmp_path, code_sink=codes.append)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:8765') as client:
                headers = {'Origin': 'http://127.0.0.1:8765', 'Content-Type': 'application/json'}
                assert (await client.post('/api/v1/session', headers=headers, content=b'{"schema_version":1,"code":1e309}')).status_code == 400
                login = await client.post('/api/v1/session', headers=headers, json={'schema_version': 1, 'code': codes[0]})
                token = login.json()['request_token']
                response = await client.get('/api/v1/library?limit=25&limit=200', headers={'X-QTE-Token': token})
                assert response.status_code == 400
    asyncio.run(scenario())
