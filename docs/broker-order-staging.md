# Broker order staging

Implemented November 19, 2026. This is the first execution-setup milestone,
not a live gateway. It makes reproducible, inspectable tickets for both brokers
without sending requests, reading credentials, choosing an account or changing
approval settings. It has no submit command or network transport.

## Try the offline workflow

From the repository root, use the existing virtual environment:

```sh
.venv/bin/python -m qte.brokers.staging stage examples/broker-draft-schwab.json
.venv/bin/python -m qte.brokers.staging stage examples/broker-draft-robinhood.json
.venv/bin/python -m qte.brokers.staging show qte-REPLACE_WITH_RETURNED_ID
```

The examples are dummy tickets, not current quotes or trade recommendations.
The ignored `build/broker-drafts.sqlite` journal retains each draft. Save the
returned client ID. Repeat `stage ... --client-id <saved-id>` to recover exactly
the same terms; changed terms under that ID fail. Omitting the ID creates a new
logical draft even if the economics match. Stable local identity is not a promise
of broker-side exactly-once execution. Do not delete the journal to retry orders.

## Supported ticket subset

- Explicit broker and a local account alias (never API tokens, app secrets or
  account numbers). Aliases are not yet resolved to authenticated accounts.
- Whole-share long equity entries/exits and single-leg options with explicit
  buy/sell and open/close. Short option drafts are representable, not risk-approved.
- Limit orders only, day duration and regular session. Quantity, price and tick
  size are explicit. Decimal strings avoid float rounding. Tick-grid checks use
  the user's declaration; broker instrument metadata must subsequently verify it.
- Robinhood options use discovered instrument UUIDs. Schwab options initially
  accept standard 21-character OCC-style provider symbols, preserving spaces.
  Syntax validation does not prove a contract exists or is tradable. Do not
  fabricate identifiers from the underlying ticker.
- Unknown/duplicate JSON fields, invalid quantities, unsupported equity shorts,
  nonfinite or off-grid prices and changed saved identities are rejected.

No spread, fractional share, futures, crypto, market, stop, extended-session or
conditional order support is implied by this initial staging subset.

## Provider handoffs and remaining work

Robinhood output names the MCP review tool and arguments, leaves account identity
unresolved and records a separate placement ref_id. No tool is invoked. An
assistant-side handoff must refresh account eligibility/approval settings,
resolve the intended account, obtain the broker review and follow its alerts and
approval workflow with the exact reviewed terms. MCP tools are not automatically
available to standalone QTE; no unofficial Robinhood login/API is used.

Schwab output is a candidate JSON body based on the maintained
[schwab-py order builder documentation](https://schwab-py.readthedocs.io/en/latest/order-builder.html).
That project describes parts of its schema as reverse engineered. Before any
POST, verify the body, endpoint, account identifier and response semantics against
the user's official [Trader API contract](https://developer.schwab.com/products/trader-api--individual).
No SDK was installed, endpoint guessed for transmission, or broker idempotency
field assumed. Existing `schwab_auth login/status/refresh` remains separate.

Next increments:

1. Verify Schwab's Accounts and Trading export and selected account authorization;
   implement read-only account/order/position reconciliation. Verify Robinhood
   MCP handoff availability and bind account aliases explicitly at runtime.
2. Bind drafts to fresh quotes, holdings, permissions, buying power, fees and
   per-order/account risk limits. Record reviewed terms and their expiry.
3. Implement the durable live gateway with pending-approval versus acknowledged
   versus filled states, uncertain submission handling, cancel reconciliation,
   disconnect/halt controls and recovery. A timeout must not trigger a blind retry.
4. Connect broker execution facts once to the authoritative accounting ledger;
   then conduct separately authorized, bounded live validation.

The existing paper coordinator deliberately rejects live accounts. Its safeguards
remain unchanged; a draft is never passed off as a paper fill or a broker order.
No strategy-to-account execution, live-ready status, or profitability is claimed.

## Assistant-operated Robinhood connection

`qte.brokers.robinhood_handoff` now bridges saved drafts to MCP tool requests and
records returned responses. A live read of the agent-enabled account established
the local alias `hood-mcp`; inspect the saved checkpoint with:

```sh
.venv/bin/python -m qte.brokers.robinhood_handoff connection hood-mcp
```

This checkpoint is historical evidence, not a continuously connected session.
Account numbers are hashed/masked in the binding table. Every review or placement
requires freshly fetched accounts and approval-setting observations (60-second
maximum age), matching the bound account. It does not store broker credentials.

The assistant host calls the module with JSON on stdin, then invokes the returned
MCP tool in its own runtime. Never put account observations in shell arguments or
tracked examples. Input shape for `connect <alias>` and `review-request <id>`:
`{"observed_at": <receipt Unix seconds>, "accounts": <get_accounts structured response>, "approval": <get_trade_approval_setting structured response>}`.
These are trusted host attestations, not cryptographic provider signatures.
Do not import hand-edited observations as actual broker evidence.

Workflow:
1. Stage the user's exact intended trade under `hood-mcp`; examples are not
   authorized trades. Refresh account/approval observations.
2. `review-request <id>` returns an allowlisted review tool and exact arguments;
   invoke it and pass the response to `record-review <id> <request-id>` on stdin.
   Review guides, disclosures and alerts are retained verbatim. Options review
   schemas can vary; the host must verify that the response matches the request.
3. Follow the broker review workflow. `placement-request <id>` takes
   `{"observations": <fresh observations>, "authorization": <user's specific trade instruction>, "confirmed_review_id": <ID when confirmation is required>}`.
   App approval with no alerts uses Robinhood's app workflow; flagged orders or
   orders without app approval require explicit confirmation of that exact review.
   This is an operator audit record, not permission to invent trading instructions.
4. The module commits `submission_uncertain` before returning placement arguments.
   Invoke the tool once and journal its response using
   `record-placement <id> <request-id>`. Approval instructions are not fills.
   `status <id>` shows the receipt without applying accounting.
5. A timeout, crash or error blocks repeat placement and re-review of that ID.
   Inspect broker approvals/orders and reconcile; no automatic retry or journal
   deletion. Even successful responses remain `fill_status=not_reconciled` until
   a future execution/ledger bridge verifies actual fills.

No live review/placement was exercised during setup: only account and approval
reads. The journal is private mode 0600 on POSIX; review/placement receipts can
contain private account information. Windows ACL hardening remains outstanding.
The assistant retains responsibility for user authorization, current instrument
eligibility, quote/session checks and the provider's review instructions. There
is no autonomous risk-approved strategy gateway, fill polling, cancellation
bridge, portfolio synchronization or continuous monitoring in this milestone.
