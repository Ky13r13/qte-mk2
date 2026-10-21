"""Exact-value wire translation for the bounded, explicit router profile."""
from dataclasses import fields

from qte.regimes import RegimeConfig
from qte.router_config import prepare_router_profile
from .artifacts import strict_json
from .research_forms import (
    _CANDIDATE_INTEGER_FIELDS, _INT64_MAX, _UINT64_MAX, _error, _integer,
)


def compile_router_profile(text):
    path = "configuration.strategy.profile"
    if type(text) is not str or not text or len(text.encode("utf-8", errors="replace")) > 12000:
        raise _error(path, "Profile must be JSON text of at most 12000 UTF-8 bytes.")
    try:
        value = strict_json(text.encode("utf-8"))
        if type(value) is not dict:
            raise ValueError
        for name in ("macro_observations", "dealer_observations"):
            rows = value.get(name, [])
            if type(rows) is not list or len(rows) > 128:
                raise ValueError
            for index, row in enumerate(rows):
                if type(row) is not dict:
                    raise ValueError
                for key in ("reference_ns", "available_ns"):
                    row[key] = _integer(row.get(key), f"{path}.{name}[{index}].{key}",
                                        minimum=-(2**63), maximum=_INT64_MAX, signed=True)
        candidates = value.get("candidates")
        if type(candidates) is not list or not 1 <= len(candidates) <= 6:
            raise ValueError
        for index, candidate in enumerate(candidates):
            if type(candidate) is not dict:
                raise ValueError
            for key in _CANDIDATE_INTEGER_FIELDS:
                if key in candidate:
                    candidate[key] = _integer(candidate[key], f"{path}.candidates[{index}].{key}",
                                              minimum=1, maximum=_UINT64_MAX)
        regime = value.get("regime", {})
        if type(regime) is not dict:
            raise ValueError
        for field in fields(RegimeConfig):
            if field.name.endswith("_ns") and field.name in regime:
                regime[field.name] = _integer(regime[field.name], f"{path}.regime.{field.name}",
                                              minimum=0, maximum=_INT64_MAX)
        prepare_router_profile(value)
        return value
    except (ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError):
        raise _error(path, "Invalid router profile; use quoted decimal integers, explicit provenance and supported fields.") from None
