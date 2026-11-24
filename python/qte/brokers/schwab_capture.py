"""Store one contract-verified Schwab market-data response for later audit."""
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
import time


def _encode(value):
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def capture_read(client, operation, output: Path, *, path_parameters, query_parameters, clock=time.time_ns):
    """Save provider JSON and collector timestamps without recording request headers.

    Receipt time bounds when this collector learned the response. Historical
    event and availability times must be established separately by an adapter.
    The output is published exclusively after the full capture is written.
    """
    if os.path.lexists(output):
        raise FileExistsError(output)
    if not output.parent.is_dir():
        raise FileNotFoundError(output.parent)
    started_ns = clock()
    response = client.get(operation, path_parameters=path_parameters, query_parameters=query_parameters)
    received_ns = clock()
    if received_ns < started_ns:
        raise ValueError('Collector clock moved backward during the market-data request.')
    response_hash = sha256(_encode(response)).hexdigest()
    record = {
        'schema_version': 1,
        'provider': 'schwab',
        'contract_sha256': client.contract_sha256,
        'request': {
            'operation_id': operation,
            'path_parameters': path_parameters,
            'query_parameters': query_parameters,
        },
        'request_started_utc_ns': started_ns,
        'response_received_utc_ns': received_ns,
        'response_canonical_json_sha256': response_hash,
        'response': response,
    }
    stream = NamedTemporaryFile(dir=output.parent, prefix='.schwab-capture-', delete=False)
    temporary = Path(stream.name)
    try:
        with stream:
            stream.write(_encode(record) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return response_hash
