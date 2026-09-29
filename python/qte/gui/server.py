"""Explicit foreground loopback launcher; no jobs, credentials or background daemon."""
from __future__ import annotations

import importlib
from pathlib import Path
import socket
from typing import Callable


def serve(repository: Path, *, port: int = 8765,
          announce: Callable[[str], object] = print) -> int:
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('GUI port must be an integer in [1, 65535]')
    repository = Path(repository).absolute()
    if not repository.is_dir():
        raise ValueError('GUI repository directory does not exist')
    try:
        uvicorn = importlib.import_module('uvicorn')
        importlib.import_module('markdown_it')
        importlib.import_module('starlette')
    except ImportError as error:
        raise ValueError(
            'GUI dependencies are unavailable; install the approved '
            'requirements-gui.lock into the repository .venv. Nothing was installed.'
        ) from error
    from .app import create_app

    # Bind before generating/printing an access code. An occupied port never
    # launches another server or silently changes the authenticated origin.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(('127.0.0.1', port))
        except OSError as error:
            raise OSError(
                f'Cannot bind QTE GUI to 127.0.0.1:{port}; '
                'check for an existing server or select --port explicitly.'
            ) from error
        listener.listen(128)
        listener.setblocking(False)
        app = create_app(repository, port=port,
                         code_sink=lambda code: announce(f'QTE local access code: {code}'))
        config = uvicorn.Config(
            app, host='127.0.0.1', port=port, workers=1, reload=False,
            proxy_headers=False, access_log=False, log_level='warning',
            loop='asyncio', http='h11', ws='none', lifespan='on',
            limit_concurrency=32, timeout_keep_alive=5,
            h11_max_incomplete_event_size=16384,
            server_header=False, date_header=False,
        )
        announce(f'QTE Library: http://127.0.0.1:{port}/library (Ctrl-C to stop)')
        try:
            uvicorn.Server(config).run(sockets=[listener])
        except KeyboardInterrupt:
            return 130
    return 0
