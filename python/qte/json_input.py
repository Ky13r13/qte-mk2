"""Strict JSON for captured configuration and research artifacts."""

import json
import math

JSON_DEPTH_LIMIT = 64


def strict_json(data: bytes | str):
    def pairs(values):
        out = {}
        for key, value in values:
            if key in out: raise ValueError("duplicate key")
            out[key] = value
        return out
    def finite(value: str) -> float:
        result = float(value)
        if not math.isfinite(result): raise ValueError("nonfinite")
        return result
    try:
        result = json.loads(data, object_pairs_hook=pairs, parse_float=finite,
                            parse_constant=finite)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the limit") from exc
    # JSON escapes can decode to lone surrogates even when the input bytes are
    # valid UTF-8. Reject them before metadata reaches response/path encoding.
    pending = [(result, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > JSON_DEPTH_LIMIT:
            raise ValueError("JSON nesting exceeds the limit")
        if isinstance(value, str):
            try: value.encode("utf-8")
            except UnicodeEncodeError as exc: raise ValueError("invalid Unicode scalar") from exc
        elif isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.keys())
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    return result
