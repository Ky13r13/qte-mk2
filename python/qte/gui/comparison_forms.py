"""Presentation metadata for the fixed comparison registry; no input access."""
from __future__ import annotations

import copy
from dataclasses import asdict

from qte.comparison_config import _historical_experiment, _legacy_experiment
from qte.strategies import CandidateConfig

from .research_forms import form_options


def _descriptor(value):
    if type(value) is int:
        return {"type": "integer_string", "default": str(value), "required": True}
    if type(value) is list:
        return {"type": "string_array", "default": list(value), "required": True}
    return {"type": "number", "default": value, "required": True}


def _experiment_fields(defaults):
    fields = {}
    for name, value in defaults.items():
        if type(value) is dict:
            fields.update({f"{name}.{key}": _descriptor(item) for key, item in value.items()})
        else:
            fields[name] = _descriptor(value)
    return fields


def comparison_form_options():
    """Preserve canonical defaults and every accepted candidate/config field."""
    strategies = copy.deepcopy(form_options()["research_run"]["strategies"])
    parameters = asdict(CandidateConfig())
    parameters.pop("kind")
    for strategy in strategies:
        if strategy["type"] != "moving_average_regime":
            strategy["fields"] = {name: _descriptor(value) for name, value in parameters.items()}
            strategy.pop("hidden_defaults", None)
    return {"schema_version": 1, "strategies": strategies, "workflows": [
        {"type": "moving_average_comparison", "label": "SMA train/test comparison",
         "min_windows": 2, "max_windows": 2, "max_candidates": 1,
         "experiment_fields": _experiment_fields(_legacy_experiment({}))},
        {"type": "historical_experiment", "label": "Frozen candidate experiment",
         "min_windows": 3, "max_windows": 24, "max_candidates": 32,
         "experiment_fields": _experiment_fields(_historical_experiment({}))},
    ]}
