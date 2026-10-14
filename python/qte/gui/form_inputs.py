"""Read-only capture of bounded inline research-form inputs before authorization."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath

from .artifacts import strict_json
from .job_inputs import (
    CONFIG_LIMIT,
    DATA_LIMIT,
    JobInputError,
    _allowed,
    _error,
    _read_regular,
    _relative,
    _safe_parts,
)
from .research_forms import INLINE_CONFIG_ORIGIN


@dataclass(frozen=True)
class CapturedFormInputs:
    """Exact captured form/data identities, with no source filename invented."""
    kind: str
    config_origin: dict[str, object]
    configuration_bytes: bytes
    config_sha256: str
    original_config_path: None
    original_data_path: str | None
    adapter: str | None
    data_bytes: bytes | None
    data_sha256: str | None

    @property
    def config_bytes(self) -> bytes:
        """Compatibility spelling for consumers shared with path-based capture."""
        return self.configuration_bytes


def _canonical_configuration(configuration: object) -> tuple[bytes, dict]:
    if not isinstance(configuration, dict):
        raise _error("invalid")
    try:
        encoded = json.dumps(configuration, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                             allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise _error("invalid") from None
    if len(encoded) > CONFIG_LIMIT:
        raise _error("invalid")
    try:
        canonical = strict_json(encoded)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise _error("invalid") from None
    if not isinstance(canonical, dict):
        raise _error("invalid")
    return encoded, canonical


def _origin(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(INLINE_CONFIG_ORIGIN):
        raise _error("invalid")
    for key, expected in INLINE_CONFIG_ORIGIN.items():
        actual = value[key]
        if type(actual) is not type(expected) or actual != expected:
            raise _error("invalid")
    return dict(INLINE_CONFIG_ORIGIN)


def capture_form_inputs(
    repository: Path,
    kind: str,
    config_origin: object,
    configuration: object,
) -> CapturedFormInputs:
    """Capture only the minimum inline envelope and exact research input bytes.

    This boundary deliberately does not parse bars, construct a strategy, or
    validate financial fields. Authorization must happen before those actions.
    """
    if not isinstance(kind, str) or kind not in {"research_run", "dataset_validate", "strategy_lab", "download_alpaca"}:
        raise _error("invalid")
    origin = _origin(config_origin)
    configuration_bytes, canonical = _canonical_configuration(configuration)
    if kind in {"strategy_lab", "download_alpaca"}:
        return CapturedFormInputs(
            kind, origin, configuration_bytes, sha256(configuration_bytes).hexdigest(), None,
            None, None, None, None,
        )

    data = canonical.get("data")
    if not isinstance(data, dict):
        raise _error("invalid")
    adapter, path = data.get("adapter"), data.get("path")
    if not isinstance(adapter, str) or adapter not in {"csv", "alpaca"} or not isinstance(path, str):
        raise _error("invalid")
    if not 1 <= len(path) <= 512:
        raise _error("invalid")
    parts = _safe_parts(path)
    _allowed(parts)
    relative = _relative(parts)
    suffix = PurePosixPath(relative).suffix.casefold()
    if (adapter == "csv" and suffix != ".csv") or (adapter == "alpaca" and suffix != ".json"):
        raise _error("invalid")

    # Preserve canonical submitted form bytes exactly. The normalized relative
    # path is distinct capture metadata; only the later snapshot boundary may
    # build an effective config with a copied-input pathname.
    data_bytes = _read_regular(Path(repository), relative, DATA_LIMIT)
    return CapturedFormInputs(
        kind, origin, configuration_bytes, sha256(configuration_bytes).hexdigest(), None,
        relative, adapter, data_bytes, sha256(data_bytes).hexdigest(),
    )
