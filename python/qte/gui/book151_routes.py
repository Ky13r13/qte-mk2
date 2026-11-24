"""Authenticated read-only access to bounded local Book151 chart artifacts."""
import re

from starlette.responses import Response
from starlette.routing import Route

from .artifacts import strict_json
from .safeio import SafeReadError, read_registered_file
from .security import SecurityError

CLOCK = r'(?:1m|5m|10m|15m|30m|daily|weekly|monthly)'
RESOURCE = re.compile(rf'(?:index|candles-{CLOCK}-(?:SPY|QQQ)|run-{CLOCK}-(?:3|4|10)\.[0-9]{{1,2}}(?:\.[12])?)\Z')


def book151_routes(repository, authenticate):
    async def read(request):
        authenticate(request, token=True)
        if request.query_params:
            raise SecurityError('invalid_query', 400, 'Chart resources take no query parameters.')
        name=request.path_params['name']
        if not RESOURCE.fullmatch(name):
            raise SecurityError('not_found', 404, 'Unknown chart resource.')
        try:
            raw=read_registered_file(repository,f'build/book151-chart/{name}.json')
            if type(strict_json(raw)) is not dict:
                raise ValueError('Expected chart object')
        except SafeReadError as exc:
            raise SecurityError(exc.code,exc.status,'Chart data unavailable. Generate the local Book151 export.') from None
        except (ValueError, UnicodeError):
            raise SecurityError('invalid_artifact',422,'Invalid chart data.') from None
        return Response(raw,media_type='application/json',headers={'Cache-Control':'no-store'})

    return [Route('/api/v1/book151-study/{name:str}',read,methods=['GET'])]
