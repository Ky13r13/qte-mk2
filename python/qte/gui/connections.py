"""Offline-only broker connection prerequisite projections.

This module deliberately enumerates environment key names only.  It does not
read, validate, retain, or expose credential values.
"""
from __future__ import annotations

import os
from collections.abc import Mapping


def connection_status(environment: Mapping[str, str] | None = None) -> dict:
    """Return the fixed offline readiness projection.

    Iteration is intentional: on some Python versions ``os._Environ`` inherits
    a membership implementation that fetches the associated value.  Scanning
    names avoids that value access and retains only the two fixed presence bits.
    """
    if environment is None:
        environment = os.environ

    schwab_present = False
    robinhood_present = False
    for name in environment:
        if name == "SCHWAB_ACCESS_TOKEN":
            schwab_present = True
        elif name == "ROBINHOOD_CRYPTO_API_KEY":
            robinhood_present = True

    return {
        "schema_version": 1,
        "mode": "offline_prerequisites_only",
        "read_enabled": False,
        "private_response_retention": False,
        "connections": [
            {
                "id": "schwab",
                "label": "Schwab",
                "scope": "Contract-defined GET only",
                "credential_environment_key": "SCHWAB_ACCESS_TOKEN",
                "credential_key_present": schwab_present,
                "credential_validity": "not_checked",
                "authentication": "not_verified",
                "read_enabled": False,
                "contract_state": "not_configured",
                "signer_state": "not_applicable",
                "operations": [],
                "blockers": [
                    "broker_reads_disabled",
                    "contract_not_configured",
                    *([] if schwab_present else ["credential_key_absent"]),
                ],
            },
            {
                "id": "robinhood_crypto",
                "label": "Robinhood Crypto",
                "scope": "Crypto GET only; no equities or trading",
                "credential_environment_key": "ROBINHOOD_CRYPTO_API_KEY",
                "credential_key_present": robinhood_present,
                "credential_validity": "not_checked",
                "authentication": "not_verified",
                "read_enabled": False,
                "contract_state": "not_applicable",
                "signer_state": "not_configured",
                "operations": ["accounts", "trading_pairs", "holdings", "orders"],
                "blockers": [
                    "broker_reads_disabled",
                    "signer_not_configured",
                    *([] if robinhood_present else ["credential_key_absent"]),
                ],
            },
        ],
    }
