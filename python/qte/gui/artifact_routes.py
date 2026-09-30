"""Authenticated metadata/catalog routes; no research or broker work starts here."""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .artifacts import ArtifactError
from .dto import artifact_value
from .requests import json_request, pagination
from .security import SecurityError


def metadata_only(detail: dict) -> dict:
    return {key: value for key, value in detail.items() if key != 'files'}


def artifact_routes(catalog, authenticate):
    # Serializing local catalog operations bounds verification concurrency and
    # avoids sharing SQLite handles across requests. Engine state is never held.
    lock = asyncio.Lock()

    async def call(method, *args):
        async with lock:
            try:
                return await run_in_threadpool(method, *args)
            except ArtifactError as exc:
                raise SecurityError(exc.code, exc.status, exc.message) from None

    def response(**values):
        return JSONResponse({'schema_version': 1, **values}, headers={'Cache-Control': 'no-store'})

    async def register(request):
        authenticate(request, token=True)
        payload = await json_request(request, {'path'})
        path = payload['path']
        if not isinstance(path, str) or not 1 <= len(path) <= 512 or any(ord(c) < 32 for c in path):
            raise SecurityError('invalid_request', 400, 'A repository-relative artifact directory is required.')
        result = await call(catalog.register, path)
        return response(artifact=artifact_value(metadata_only(result)))

    async def listing(request):
        authenticate(request, token=True)
        offset, limit = pagination(request)
        items, total = await call(catalog.list, offset, limit)
        return response(items=artifact_value([metadata_only(item) for item in items]), total=total,
                        offset=offset, limit=limit)

    async def detail(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError('invalid_query', 400, 'This route does not accept query parameters.')
        result = await call(catalog.get, request.path_params['artifact_id'])
        return response(artifact=artifact_value(metadata_only(result)))

    async def files(request):
        authenticate(request, token=True)
        offset, limit = pagination(request)
        result = await call(catalog.get, request.path_params['artifact_id'])
        items = result['files']
        return response(items=artifact_value(items[offset:offset + limit]), total=len(items),
                        offset=offset, limit=limit)

    async def reveal(request):
        authenticate(request, token=True)
        payload = await json_request(request, {'confirmation'})
        if payload['confirmation'] != 'reveal outside protocol':
            raise SecurityError('confirmation_required', 400, 'Explicit holdout confirmation is required.')
        result = await call(catalog.reveal, request.path_params['artifact_id'], payload['confirmation'])
        return response(artifact=artifact_value(metadata_only(result)))

    async def download(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError('invalid_query', 400, 'This route does not accept query parameters.')
        data, metadata = await call(catalog.read_file, request.path_params['artifact_id'], request.path_params['file_id'])
        filename = quote(metadata['name'].rsplit('/', 1)[-1], safe='')
        return Response(data, media_type='application/octet-stream', headers={
            'Cache-Control': 'no-store', 'Content-Disposition': f"attachment; filename*=UTF-8''{filename}",
            'X-Content-Type-Options': 'nosniff',
        })

    def views():
        from .run_views import RunViews
        return RunViews(catalog)

    async def run_detail(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError('invalid_query', 400, 'This route does not accept query parameters.')
        result = await call(views().detail, request.path_params['artifact_id'])
        return response(run=artifact_value(result))

    async def series(request):
        authenticate(request, token=True)
        query = request.query_params
        allowed = {'sampling', 'max_points', 'start_ns', 'end_ns'}
        if any(key not in allowed or len(query.getlist(key)) != 1 for key in query):
            raise SecurityError('invalid_query', 400, 'Invalid series query.')
        sampling = query.get('sampling', 'event')
        size = query.get('max_points', '2000')
        if sampling not in {'event', 'sampled'} or not re.fullmatch(r'[0-9]{1,4}', size) or not 4 <= int(size) <= 2000:
            raise SecurityError('invalid_query', 400, 'Invalid series sampling or point limit.')
        def timestamp(key):
            value = query.get(key)
            if value is None:
                return None
            if len(value) > 20 or not re.fullmatch(r'-?(0|[1-9][0-9]*)', value) or not -(2**63) <= int(value) < 2**63:
                raise SecurityError('invalid_query', 400, 'Timestamps must be signed 64-bit decimal strings.')
            return int(value)
        start, end = timestamp('start_ns'), timestamp('end_ns')
        if start is not None and end is not None and start > end:
            raise SecurityError('invalid_query', 400, 'Series range is reversed.')
        result = await call(views().series, request.path_params['artifact_id'], sampling, int(size), start, end)
        return response(series=artifact_value(result))

    async def run_table(request):
        authenticate(request, token=True)
        offset, limit = pagination(request)
        name = request.path_params['table_name']
        if name not in {'orders', 'fills', 'equity', 'sampled_equity',
                        'positions', 'closed_trades', 'open_trades', 'order_events'}:
            raise SecurityError('not_recorded', 404, 'This table is not recorded in the selected format.')
        result = await call(views().table, request.path_params['artifact_id'], name, offset, limit)
        # Pagination is bounded API metadata; artifact values stay decimal text.
        paging = {key: result[key] for key in ('total', 'offset', 'limit')}
        return response(**{**artifact_value(result), **paging})

    def experiment_query(request, *, table=False):
        query = request.query_params
        if table:
            offset, limit = pagination(request, extra={'timeframe', 'window'})
        else:
            if any(key != 'timeframe' or len(query.getlist(key)) != 1 for key in query):
                raise SecurityError('invalid_query', 400, 'Invalid experiment query.')
            offset, limit = 0, 25
        timeframe = query.get('timeframe')
        window = query.get('window')
        if timeframe is not None and timeframe not in {'hourly', 'daily_24h'}:
            raise SecurityError('invalid_query', 400, 'Invalid experiment timeframe.')
        if window is not None and (not 1 <= len(window) <= 128 or any(ord(c) < 32 for c in window)):
            raise SecurityError('invalid_query', 400, 'Invalid experiment window.')
        return timeframe, window, offset, limit

    async def experiment_detail(request):
        authenticate(request, token=True)
        timeframe, _, _, _ = experiment_query(request)
        result = await call(catalog.experiment_view, request.path_params['artifact_id'], timeframe)
        return response(experiment=artifact_value(result))

    async def experiment_table(request):
        authenticate(request, token=True)
        timeframe, window, offset, limit = experiment_query(request, table=True)
        name = request.path_params['table_name']
        if name not in {'candidates', 'windows', 'comparison', 'macro', 'regime_decisions'}:
            raise SecurityError('not_recorded', 404, 'Experiment table unavailable.')
        if name in {'candidates', 'windows'} and window is not None:
            raise SecurityError('invalid_query', 400, 'This table does not accept a window.')
        result = await call(catalog.experiment_view, request.path_params['artifact_id'],
                            timeframe, name, window, offset, limit)
        paging = {key: result[key] for key in ('total', 'offset', 'limit')}
        return response(**{**artifact_value(result), **paging})

    return [
        Route('/api/v1/catalog/register', register, methods=['POST']),
        Route('/api/v1/artifacts', listing, methods=['GET']),
        Route('/api/v1/artifacts/{artifact_id:str}', detail, methods=['GET']),
        Route('/api/v1/artifacts/{artifact_id:str}/files', files, methods=['GET']),
        Route('/api/v1/artifacts/{artifact_id:str}/reveal-holdout', reveal, methods=['POST']),
        Route('/api/v1/artifacts/{artifact_id:str}/files/{file_id:str}/download', download, methods=['GET']),
        Route('/api/v1/runs/{artifact_id:str}', run_detail, methods=['GET']),
        Route('/api/v1/runs/{artifact_id:str}/series', series, methods=['GET']),
        Route('/api/v1/runs/{artifact_id:str}/tables/{table_name:str}', run_table, methods=['GET']),
        Route('/api/v1/experiments/{artifact_id:str}', experiment_detail, methods=['GET']),
        Route('/api/v1/experiments/{artifact_id:str}/tables/{table_name:str}', experiment_table, methods=['GET']),
    ]
