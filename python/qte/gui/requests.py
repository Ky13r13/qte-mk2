"""Small bounded request contracts shared by the session and catalog routes."""
from __future__ import annotations

import json
import math

from starlette.requests import Request

from .security import SecurityError, unique_header


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('nonfinite JSON value')
    return result


def strict_json(raw: bytes):
    def invalid(_):
        raise ValueError('nonfinite JSON constant')
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                      parse_float=_finite, parse_constant=invalid)


async def json_request(request: Request, fields: set[str], *, limit: int = 4096) -> dict:
    content_type = unique_header(request.scope, b'content-type') or ''
    if content_type.split(';', 1)[0].strip().lower() != 'application/json':
        raise SecurityError('unsupported_media_type', 415, 'Content-Type must be application/json.')
    declared = unique_header(request.scope, b'content-length')
    if declared is not None:
        if not declared.isascii() or not declared.isdecimal():
            raise SecurityError('invalid_request', 400, 'Invalid Content-Length header.')
        if len(declared) > 10 or int(declared) > limit:
            raise SecurityError('request_too_large', 413, 'Request body exceeds the size limit.')
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise SecurityError('request_too_large', 413, 'Request body exceeds the size limit.')
        chunks.append(chunk)
    try:
        payload = strict_json(b''.join(chunks))
        if not isinstance(payload, dict) or set(payload) != fields | {'schema_version'}:
            raise ValueError('shape')
        if type(payload['schema_version']) is not int or payload['schema_version'] != 1:
            raise ValueError('version')
    except (ValueError, UnicodeError, RecursionError):
        raise SecurityError('invalid_request', 400, 'Invalid request object.') from None
    return payload


def pagination(request: Request, *, extra: set[str] = frozenset()) -> tuple[int, int]:
    query = request.query_params
    if any(key not in {'offset', 'limit'} | extra or len(query.getlist(key)) != 1 for key in query):
        raise SecurityError('invalid_query', 400, 'Invalid query parameters.')
    values = [query.get('offset', '0'), query.get('limit', '25')]
    if any(not value.isascii() or not value.isdecimal() or len(value) > 9 for value in values):
        raise SecurityError('invalid_query', 400, 'Invalid pagination query.')
    offset, limit = map(int, values)
    if not 1 <= limit <= 200:
        raise SecurityError('invalid_query', 400, 'Invalid pagination query.')
    return offset, limit
