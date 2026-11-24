"""Durable assistant-operated Robinhood MCP handoff; no network or autonomous trading.

The host executes returned tool requests and returns their structured responses.
No credentials or executable provider URLs are accepted by this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

from .staging import DraftJournal, _unique_fields


MAX_AGE = 60
MAX_RESPONSE = 4 * 1024 * 1024


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _structured(response):
    if not isinstance(response, dict) or response.get("isError"):
        raise ValueError("MCP error or invalid response")
    result = response.get("structuredContent", response)
    if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
        raise ValueError("MCP structured data is required")
    return result


class RobinhoodHandoff(DraftJournal):
    def __init__(self, path: Path, *, clock=time.time):
        if os.name == "posix":
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                if os.fstat(fd).st_uid != os.getuid():
                    raise ValueError("handoff journal must belong to this user")
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
        super().__init__(path)
        self.clock = clock
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS hood_accounts(
            alias TEXT PRIMARY KEY, account_hash TEXT UNIQUE NOT NULL,
            last4 TEXT NOT NULL, observed_at REAL NOT NULL, evidence TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS hood_handoffs(
            client_id TEXT PRIMARY KEY REFERENCES order_drafts(client_id),
            fingerprint TEXT NOT NULL, account_hash TEXT NOT NULL,
            request_id TEXT NOT NULL, state TEXT NOT NULL,
            requested_at REAL NOT NULL, reviewed_at REAL,
            review TEXT, receipt TEXT, authorization TEXT);
          CREATE TABLE IF NOT EXISTS hood_handoff_events(
            sequence INTEGER PRIMARY KEY, client_id TEXT, at REAL NOT NULL,
            event TEXT NOT NULL, detail TEXT NOT NULL);
        """)

    def _fresh(self, observed_at):
        if type(observed_at) not in (int, float) or not 0 <= self.clock() - observed_at <= MAX_AGE:
            raise ValueError("fresh MCP observations required (at most 60 seconds old)")

    def _observations(self, observations):
        self._fresh(observations.get("observed_at"))
        accounts = _structured(observations.get("accounts"))["data"].get("accounts")
        if not isinstance(accounts, list) or any(not isinstance(a, dict) for a in accounts):
            raise ValueError("invalid accounts response")
        eligible = [a for a in accounts if a.get("agentic_allowed") is True]
        if len(eligible) != 1:
            raise ValueError("one unambiguous agentic account is required")
        account = eligible[0]
        number = account.get("account_number")
        if not isinstance(number, str) or not re.fullmatch(r"[A-Za-z0-9]{4,64}", number):
            raise ValueError("invalid account identifier")
        if account.get("state") != "active" or account.get("deactivated") is not False or account.get("permanently_deactivated") is not False:
            raise ValueError("account must be explicitly active")
        setting = _structured(observations.get("approval"))["data"].get("setting")
        if not isinstance(setting, dict) or setting.get("account_number") != number:
            raise ValueError("approval response does not match account")
        if type(setting.get("human_must_approve_trades")) is not bool:
            raise ValueError("approval setting is unknown")
        return account, setting

    def connect(self, alias, observations):
        if not isinstance(alias, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", alias):
            raise ValueError("invalid local account alias")
        account, setting = self._observations(observations)
        number = account["account_number"]
        identity = _digest(number)
        # Persist only a masked account checkpoint, not the raw accounts payload.
        evidence = {"option_level": account.get("option_level"),
                    "manual_approval_observed": setting["human_must_approve_trades"],
                    "approval_setting_updated_at": setting.get("updated_at")}
        with self.db:
            self.db.execute("UPDATE hood_accounts SET alias=alias WHERE alias=?", (alias,))
            row = self.db.execute("SELECT account_hash FROM hood_accounts WHERE alias=?", (alias,)).fetchone()
            if row and row[0] != identity:
                raise ValueError("alias already belongs to another account")
            self.db.execute("INSERT INTO hood_accounts VALUES (?,?,?,?,?) ON CONFLICT(alias) DO UPDATE SET "
                            "observed_at=excluded.observed_at,evidence=excluded.evidence",
                            (alias, identity, number[-4:], observations["observed_at"], _encoded(evidence)))
        return self.connection(alias)

    def connection(self, alias):
        row = self.db.execute("SELECT last4,observed_at,evidence FROM hood_accounts WHERE alias=?", (alias,)).fetchone()
        if row is None:
            raise ValueError("account alias has not been connected")
        return {"account_alias": alias, "account_last4": row[0], "observed_at": row[1],
                "observations": json.loads(row[2]), "transport": "assistant_mcp_handoff",
                "autonomous_execution": False, "continuous_monitoring": False,
                "current_provider_state": "refresh_required"}

    def _bound(self, client_id, observations):
        saved = self.show(client_id)
        draft = saved["draft"]
        if draft["broker"] != "robinhood":
            raise ValueError("Robinhood draft required")
        account, setting = self._observations(observations)
        row = self.db.execute("SELECT account_hash FROM hood_accounts WHERE alias=?",
                              (draft["account_alias"],)).fetchone()
        if row is None or row[0] != _digest(account["account_number"]):
            raise ValueError("fresh account differs from bound draft account")
        if draft["asset"] == "option" and account.get("option_level") not in ("option_level_2", "option_level_3"):
            raise ValueError("options permission required")
        return saved, account, setting

    def _event(self, client_id, event, detail):
        self.db.execute("INSERT INTO hood_handoff_events(client_id,at,event,detail) VALUES (?,?,?,?)",
                        (client_id, self.clock(), event, _encoded(detail)))

    def review_request(self, client_id, observations):
        saved, account, _ = self._bound(client_id, observations)
        request_id = uuid.uuid4().hex
        # No implicit review of arbitrary example tickets: caller chooses an ID.
        with self.db:
            self.db.execute("UPDATE hood_handoffs SET state=state WHERE client_id=?", (client_id,))
            row = self.db.execute("SELECT state FROM hood_handoffs WHERE client_id=?", (client_id,)).fetchone()
            if row and row[0] in ("submission_uncertain", "broker_response_recorded"):
                raise ValueError("submission already attempted; reconcile instead of issuing another order")
            self.db.execute("INSERT INTO hood_handoffs VALUES (?,?,?,?,?,?,NULL,NULL,NULL,NULL) "
                            "ON CONFLICT(client_id) DO UPDATE SET request_id=excluded.request_id,"
                            "state=excluded.state,requested_at=excluded.requested_at,reviewed_at=NULL,review=NULL",
                            (client_id, saved["fingerprint"], _digest(account["account_number"]),
                             request_id, "awaiting_review", self.clock()))
            self._event(client_id, "review_requested", {"request_id": request_id})
        payload = saved["provider_payload_draft"]
        return {"request_id": request_id, "tool": "mcp__hood__" + payload["review_tool"],
                "arguments": dict(payload["review_arguments"], account_number=account["account_number"])}

    def record_review(self, client_id, request_id, response):
        structured = _structured(response)
        raw = _encoded(structured)
        if len(raw.encode()) > MAX_RESPONSE:
            raise ValueError("review response too large")
        data = structured["data"]
        if not isinstance(data.get("order_checks"), dict) or not isinstance(structured.get("guide"), str):
            raise ValueError("review alerts and workflow guide required")
        if data.get("customer_approval_required") is not None and type(data["customer_approval_required"]) is not bool:
            raise ValueError("invalid approval response")
        saved = self.show(client_id)
        if saved["draft"]["asset"] == "equity":
            expected = saved["provider_payload_draft"]["review_arguments"]
            for key in ("symbol", "side", "type", "quantity", "limit_price"):
                # Numeric string formatting is allowed to differ, not economics.
                actual = data.get(key)
                if key in ("quantity", "limit_price"):
                    from decimal import Decimal, InvalidOperation
                    try:
                        same = Decimal(str(actual)) == Decimal(expected[key])
                    except InvalidOperation:
                        same = False
                else:
                    same = actual == expected[key]
                if not same:
                    raise ValueError("review does not match draft")
        with self.db:
            self.db.execute("UPDATE hood_handoffs SET state=state WHERE client_id=?", (client_id,))
            row = self.db.execute("SELECT state,requested_at FROM hood_handoffs WHERE client_id=? AND request_id=?",
                                  (client_id, request_id)).fetchone()
            if row is None or row[0] != "awaiting_review":
                raise ValueError("unknown, superseded or already consumed review request")
            self._fresh(row[1])
            self.db.execute("UPDATE hood_handoffs SET state='reviewed',reviewed_at=?,review=? WHERE client_id=?",
                            (self.clock(), raw, client_id))
            self._event(client_id, "review_recorded", {"request_id": request_id})
        return {"state": "reviewed", "review": structured,
                "note": "Follow the provider guide; this is not a fill or authorization."}

    def placement_request(self, client_id, observations, *, authorization,
                          confirmed_review_id=None):
        """Host-only supervised handoff. Authorization records the user's trade instruction.

        This function reserves the attempt BEFORE exposing executable arguments.
        A crash or lost response leaves uncertainty and blocks repeat submission.
        """
        if not isinstance(authorization, str) or not 1 <= len(authorization.strip()) <= 2000:
            raise ValueError("explicit user trade instruction must be recorded")
        saved, account, setting = self._bound(client_id, observations)
        with self.db:
            # Acquire the SQLite writer lock before checking the state.
            self.db.execute("UPDATE hood_handoffs SET state=state WHERE client_id=?", (client_id,))
            row = self.db.execute("SELECT state,reviewed_at,review,request_id,fingerprint,account_hash "
                                  "FROM hood_handoffs WHERE client_id=?", (client_id,)).fetchone()
            if row is None or row[0] != "reviewed":
                raise ValueError("a fresh review is required; never blindly retry a submission")
            self._fresh(row[1])
            if row[4] != saved["fingerprint"] or row[5] != _digest(account["account_number"]):
                raise ValueError("review binding mismatch")
            review = json.loads(row[2])["data"]
            # Only the provider's per-order result permits app confirmation.
            app_approval = review.get("customer_approval_required") is True
            if review.get("customer_approval_required_reason") == "account_setting" and not setting["human_must_approve_trades"]:
                raise ValueError("approval setting changed; review again")
            if (review["order_checks"] or not app_approval) and confirmed_review_id != row[3]:
                raise ValueError("present the provider review/alerts and record confirmation of this review ID")
            self.db.execute("UPDATE hood_handoffs SET state='submission_uncertain',authorization=? WHERE client_id=?",
                            (authorization, client_id))
            self._event(client_id, "submission_reserved", {"review_id": row[3],
                        "confirmed_review_id": confirmed_review_id})
        payload = saved["provider_payload_draft"]
        return {"request_id": row[3], "tool": "mcp__hood__place_" + saved["draft"]["asset"] + "_order",
                "arguments": dict(payload["review_arguments"], account_number=account["account_number"], ref_id=client_id),
                "note": "Execute once through the host; retain ref_id for reconciliation. Response is not assumed filled."}

    def record_placement(self, client_id, request_id, response):
        # Placement can return approval instructions in content rather than a
        # structured order. Preserve them without inventing a broker state.
        if not isinstance(response, dict) or not any(k in response for k in ("content", "structuredContent", "data")):
            raise ValueError("MCP response required")
        raw = _encoded(response)
        if len(raw.encode()) > MAX_RESPONSE:
            raise ValueError("placement response too large")
        with self.db:
            self.db.execute("UPDATE hood_handoffs SET state=state WHERE client_id=?", (client_id,))
            row = self.db.execute("SELECT state,receipt FROM hood_handoffs WHERE client_id=? AND request_id=?",
                                  (client_id, request_id)).fetchone()
            if row is None or row[0] not in ("submission_uncertain", "broker_response_recorded"):
                raise ValueError("unknown submission")
            if row[1] is not None:
                if row[1] != raw:
                    raise ValueError("conflicting placement receipt; retain original and reconcile")
            else:
                state = "submission_uncertain" if response.get("isError") else "broker_response_recorded"
                self.db.execute("UPDATE hood_handoffs SET state=?,receipt=? WHERE client_id=?", (state, raw, client_id))
                self._event(client_id, "placement_response", {"is_error": bool(response.get("isError"))})
        return self.status(client_id)

    def status(self, client_id):
        self.show(client_id)
        row = self.db.execute("SELECT state,request_id,reviewed_at,receipt FROM hood_handoffs WHERE client_id=?",
                              (client_id,)).fetchone()
        return {"client_id": client_id, "state": row[0] if row else "not_handed_off",
                "request_id": row[1] if row else None, "reviewed_at": row[2] if row else None,
                "receipt": json.loads(row[3]) if row and row[3] else None,
                "fill_status": "not_reconciled", "accounting_applied": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, default=Path("build/broker-drafts.sqlite"))
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("connect", "connection"):
        sub.add_parser(name).add_argument("alias")
    for name in ("review-request", "record-review", "placement-request", "record-placement", "status"):
        p = sub.add_parser(name)
        p.add_argument("client_id")
        if name.startswith("record-"):
            p.add_argument("request_id")
    args = parser.parse_args(argv)
    try:
        data = {}
        if args.command not in ("connection", "status"):
            raw = sys.stdin.read(MAX_RESPONSE + 1)
            if len(raw.encode()) > MAX_RESPONSE:
                raise ValueError("input too large")
            data = json.loads(raw, object_pairs_hook=_unique_fields)
            if not isinstance(data, dict):
                raise ValueError("input must be an object")
        if args.command != "connect" and not args.journal.is_file():
            raise ValueError("journal does not exist")
        args.journal.parent.mkdir(parents=True, exist_ok=True)
        with RobinhoodHandoff(args.journal) as bridge:
            command = args.command.replace("-", "_")
            if command == "connect":
                result = bridge.connect(args.alias, data)
            elif command == "connection":
                result = bridge.connection(args.alias)
            elif command == "status":
                result = bridge.status(args.client_id)
            elif command == "review_request":
                result = bridge.review_request(args.client_id, data)
            elif command == "placement_request":
                result = bridge.placement_request(args.client_id, data["observations"],
                    authorization=data["authorization"], confirmed_review_id=data.get("confirmed_review_id"))
            else:
                result = getattr(bridge, command)(args.client_id, args.request_id, data)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        parser.exit(2, "qte hood: handoff rejected; check account binding, freshness, review and durable state. No MCP tool called by this command.\n")


if __name__ == "__main__":
    raise SystemExit(main())
