"""Offline, durable order drafts for Schwab and Robinhood MCP. Never submits."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import uuid


def _price(value: str) -> Decimal:
    # Require decimal strings: floats cannot silently change a money value.
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,8})?", value):
        raise ValueError("prices and tick sizes must be positive plain decimal strings")
    result = Decimal(value)
    if result <= 0:
        raise ValueError("prices and tick sizes must be positive")
    return result


@dataclass(frozen=True)
class OrderDraft:
    broker: str
    account_alias: str
    asset: str
    instrument: str
    side: str
    position_effect: str
    quantity: int
    limit_price: str
    tick_size: str

    def __post_init__(self):
        if self.broker not in ("schwab", "robinhood"):
            raise ValueError("broker must be schwab or robinhood")
        if not isinstance(self.account_alias, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", self.account_alias):
            raise ValueError("use a local account alias, not credentials or an account number")
        if self.asset not in ("equity", "option"):
            raise ValueError("asset must be equity or option")
        if self.side not in ("buy", "sell") or self.position_effect not in ("open", "close"):
            raise ValueError("explicit side and position_effect required")
        if type(self.quantity) is not int or not 1 <= self.quantity <= 1_000_000:
            raise ValueError("quantity must be a positive whole number no greater than 1000000")
        price, tick = _price(self.limit_price), _price(self.tick_size)
        if price % tick:
            raise ValueError("limit price is not on the declared tick grid")
        object.__setattr__(self, "limit_price", format(price.normalize(), "f"))
        object.__setattr__(self, "tick_size", format(tick.normalize(), "f"))
        if not isinstance(self.instrument, str):
            raise ValueError("instrument must be a provider identifier")
        if self.asset == "equity":
            if not re.fullmatch(r"[A-Z][A-Z0-9./-]{0,14}", self.instrument):
                raise ValueError("invalid equity symbol; preserve provider spelling")
            if (self.side, self.position_effect) not in (("buy", "open"), ("sell", "close")):
                raise ValueError("equity staging supports long entry/exit only")
        elif self.broker == "robinhood":
            try:
                parsed = uuid.UUID(self.instrument)
            except ValueError:
                raise ValueError("Robinhood options require the discovered instrument UUID") from None
            if str(parsed) != self.instrument or parsed.int == 0:
                raise ValueError("Robinhood option UUID must use canonical spelling and be nonzero")
        else:
            # Only standard 21-character OCC-style symbols in this initial subset.
            if not re.fullmatch(r"[A-Z][A-Z0-9. ]{5}[0-9]{6}[CP][0-9]{8}", self.instrument):
                raise ValueError("Schwab options require a discovered 21-character option symbol")
            datetime.strptime(self.instrument[6:12], "%y%m%d")
            if int(self.instrument[13:]) == 0:
                raise ValueError("option strike must be positive")

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


def preview(draft: OrderDraft, client_id: str) -> dict:
    """Produce review material, with no credentials, account resolution or I/O."""
    _identity(client_id)
    if draft.broker == "robinhood":
        args = {"quantity": str(draft.quantity), "type": "limit",
                "time_in_force": "gfd", "market_hours": "regular_hours"}
        if draft.asset == "equity":
            args.update(symbol=draft.instrument, side=draft.side, limit_price=draft.limit_price)
        else:
            args.update(price=draft.limit_price, legs=[{"option_id": draft.instrument,
                        "side": draft.side, "position_effect": draft.position_effect,
                        "ratio_quantity": 1}])
        payload = {"review_tool": f"review_{draft.asset}_order", "review_arguments": args,
                   "placement_ref_id": client_id,
                   "account_number": "UNRESOLVED_FROM_ACCOUNT_ALIAS"}
    else:
        instruction = draft.side.upper()
        if draft.asset == "option":
            instruction += "_TO_" + draft.position_effect.upper()
        payload = {"orderType": "LIMIT", "session": "NORMAL", "duration": "DAY",
                   "price": draft.limit_price, "orderStrategyType": "SINGLE",
                   "orderLegCollection": [{"instruction": instruction,
                     "quantity": draft.quantity, "instrument": {
                         "symbol": draft.instrument, "assetType": draft.asset.upper()}}]}
    return {"schema_version": 1, "state": "draft", "live_submission_enabled": False,
            "client_id": client_id, "fingerprint": draft.fingerprint,
            "draft": asdict(draft), "provider_payload_draft": payload,
            "blockers": ["account alias and trading permissions not verified",
                         "instrument, tick, quote freshness and session not verified",
                         "positions, buying power, risk and fees not reviewed",
                         "no live submission or reconciliation bridge installed"] + (
                         ["Schwab payload requires official contract verification; no broker idempotency assumed"]
                         if draft.broker == "schwab" else
                         ["MCP available only in the assistant runtime; current approval setting and broker review required"])}


def _identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"qte-[0-9a-f]{32}", value):
        raise ValueError("client ID must be qte- followed by a lowercase UUID hex value")


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate ticket field")
        result[key] = value
    return result


class DraftJournal:
    """A separate draft store; never reuses or relaxes the paper coordinator."""

    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS order_drafts ("
                        "client_id TEXT PRIMARY KEY, payload TEXT NOT NULL, fingerprint TEXT NOT NULL)")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def stage(self, draft: OrderDraft, *, client_id: str | None = None) -> dict:
        client_id = "qte-" + uuid.uuid4().hex if client_id is None else client_id
        _identity(client_id)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO order_drafts VALUES (?, ?, ?)",
                            (client_id, draft.canonical_json(), draft.fingerprint))
            saved = self.show(client_id)
            if saved["fingerprint"] != draft.fingerprint:
                raise ValueError("client ID already belongs to a different draft; original preserved")
        return saved

    def show(self, client_id: str) -> dict:
        _identity(client_id)
        row = self.db.execute("SELECT payload, fingerprint FROM order_drafts WHERE client_id=?",
                              (client_id,)).fetchone()
        if row is None:
            raise ValueError("unknown draft ID")
        draft = OrderDraft(**json.loads(row[0]))
        if draft.fingerprint != row[1]:
            raise ValueError("draft integrity check failed")
        return preview(draft, client_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=Path("build/broker-drafts.sqlite"))
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage", help="Validate and save a draft; no broker call")
    stage.add_argument("ticket", type=Path)
    stage.add_argument("--client-id", help="Reuse a saved identity only for exactly the same draft")
    show = sub.add_parser("show", help="Show an existing draft and unresolved prerequisites")
    show.add_argument("client_id")
    args = parser.parse_args(argv)
    try:
        draft = None
        if args.command == "stage":
            with args.ticket.open("rb") as source:
                raw = source.read(16385)
            if len(raw) > 16384:
                raise ValueError("ticket exceeds 16 KiB")
            draft = OrderDraft(**json.loads(raw, object_pairs_hook=_unique_fields))
        if args.command == "show" and not args.journal.is_file():
            raise ValueError("draft journal does not exist")
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        with DraftJournal(args.journal) as journal:
            result = journal.stage(draft, client_id=args.client_id) if draft else journal.show(args.client_id)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, TypeError, sqlite3.Error):
        parser.exit(2, "qte staging: invalid ticket or inaccessible/inconsistent journal; no broker request made\n")


if __name__ == "__main__":
    raise SystemExit(main())
