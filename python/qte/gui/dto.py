"""Exact-value HTTP presentation; no financial calculation belongs here."""
from __future__ import annotations

import math


def artifact_value(value):
    """Convert artifact integers to decimal text, including nested timestamps/seeds.

    API envelope versions and bounded pagination counts are added by the caller.
    Keeping every artifact integer as text avoids a fragile field-name allowlist.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('nonfinite artifact value')
        return value
    if isinstance(value, dict):
        return {key: artifact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [artifact_value(item) for item in value]
    raise TypeError('unsupported artifact value')
