"""Publish a complete UTF-8 JSON snapshot in a single same-directory replacement."""
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile


def publish(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as staging:
        temporary = Path(staging.name)
        try:
            json.dump(payload, staging, allow_nan=False, separators=(",", ":"), sort_keys=True)
            staging.write("\n")
            staging.flush()
            os.fsync(staging.fileno())
        except BaseException:
            staging.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
