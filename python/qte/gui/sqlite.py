"""Owned SQLite connections and correctly escaped read-only URI names."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3


def readonly_uri(path) -> str:
    """Build a lexical absolute SQLite URI without touching the filesystem."""
    value = Path(path).absolute()
    return value.as_uri() + "?mode=ro"


@contextmanager
def owned_connection(path, *, readonly: bool = False, timeout: float = 5,
                     row_factory=sqlite3.Row):
    """Own one SQLite transaction and close it on every post-open exit path."""
    database = readonly_uri(path) if readonly else path
    db = sqlite3.connect(database, uri=readonly, timeout=timeout)
    try:
        db.row_factory = row_factory
        with db:
            yield db
    finally:
        db.close()
