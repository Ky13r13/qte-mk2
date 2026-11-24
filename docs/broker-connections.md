# Read-only broker connection foundations

Order execution setup now has a separate [offline staging command](broker-order-staging.md)
for Schwab and Robinhood MCP tickets. These read-only clients remain unchanged;
the staging command cannot submit orders.

These Python adapters return **raw provider JSON**, outside the deterministic C++
core. They do not create canonical datasets, submit/cancel orders, reconcile the
portfolio, or connect a strategy to an account. No authenticated account request
has been exercised during development; tests use injected transports. These are
production-service read interfaces, **not paper-trading implementations**.

## Robinhood: official crypto API only

`qte.brokers.RobinhoodCryptoReadOnlyClient` uses the fixed HTTPS host
`trading.robinhood.com`. Its supported GET methods are:

| Python method | Provider path |
|---|---|
| `accounts(version=2)` | `/api/v2/crypto/trading/accounts/` |
| `accounts(version=1)` | `/api/v1/crypto/trading/accounts/` |
| `trading_pairs(symbols=(), cursor=None, limit=100)` | `/api/v1/crypto/trading/trading_pairs/` |
| `holdings(asset_codes=(), cursor=None, limit=100)` | `/api/v1/crypto/trading/holdings/` |
| `orders(cursor=None, limit=100)` | `/api/v1/crypto/trading/orders/` |

The official documentation specifies API-key, Unix-second timestamp, and
base64-encoded Ed25519 signature headers. The signed UTF-8 message concatenates
API key, timestamp, exact request path including encoded query, method, and body;
GET has an empty body. Requests must reach the service within its timestamp
validity window. [Official Robinhood Crypto API documentation](https://docs.robinhood.com/crypto/trading/).

QTE requires an injected `signer(message: bytes) -> bytes` returning the 64 raw
signature bytes. It does not implement cryptography, generate/store private keys,
or install a signing dependency. Use an audited Ed25519 implementation and request
approval before installing anything absent from `.venv`. The injected clock is
for transport authentication, never for deterministic backtest decisions.

```python
import os
from qte.brokers import RobinhoodCryptoReadOnlyClient

# Supply this callable from your approved key-management/signing implementation:
# sign_ed25519(message: bytes) -> 64 raw signature bytes.
client = RobinhoodCryptoReadOnlyClient(
    api_key=os.environ["ROBINHOOD_CRYPTO_API_KEY"],
    signer=sign_ed25519,
)
# Explicit account-read call when ready; construction does not fetch data:
# accounts = client.accounts()
```

Request cursors explicitly; the client does not automatically follow returned
pagination URLs. Returned pages are not a guarantee of complete holdings/order
history. Robinhood crypto is separate from QTE's current cash-equity/ETF,
whole-share instrument contract. There is **no Robinhood equities connector**,
unofficial login automation, fractional-crypto accounting, or crypto strategy
execution in this milestone.

## Schwab: explicit market-data endpoint definitions

The public [Schwab Trader API product page](https://developer.schwab.com/products/trader-api--individual)
identifies account/trading and market-data products. Its detailed production
specifications are available in the signed-in developer portal. The CLI takes
an explicit OpenAPI-format endpoint definition. An official export can supply
it where available. The first market-data pilot used a reviewed two-route file
derived from public client source and verified both routes against Schwab.

`SchwabReadOnlyClient(access_token, openapi=None, transport=None)` requires an
explicit OpenAPI-format file before requests can execute. Its
`get(operation_id, path_parameters={}, query_parameters={})` resolves only a GET
operation from that contract, restricted to the approved `api.schwabapi.com`
server and trader/market-data namespaces. Missing contracts fail explicitly.
An operation identifier is taken from the export, not invented by QTE.
The supported contract subset has one top-level server, named GET operations,
complete path-segment templates, and inline path/query parameters. Resolve
parameter `$ref` entries in a reviewed copy before loading; server overrides,
header/cookie parameters and ambiguous operations are rejected. Query values
are scalar strings/numbers/booleans (encode provider lists as the documented
string form). This is endpoint/parameter-name validation, not full OpenAPI schema
or provider-response validation. `client.operations` lists available names;
`client.contract_sha256` fingerprints the supplied contract snapshot.

```python
import json
import os
from pathlib import Path
from qte.brokers import SchwabReadOnlyClient

contract = json.loads(Path(".cache/brokers/schwab-official-openapi.json").read_text())
client = SchwabReadOnlyClient(
    access_token=os.environ["SCHWAB_ACCESS_TOKEN"],
    openapi=contract,
)
# Explicit read when ready, using a verified GET operationId from your export:
# payload = client.get(operation_id_from_contract)
```

The caller is responsible for verifying the supplied endpoint definitions.
Parsing a document does not authenticate its provenance. The example uses the
already-ignored repository `.cache` directory.
Keep the export and credentials local and out of shared reports.
The separate terminal command below now handles local OAuth, token storage and
explicit refresh. The GET-only client itself still performs none of those steps.
Schwab app configuration and the consent screen determine provider permissions.

### Local Schwab authorization (Fedora/Linux)

The app must show **Ready for Use**, with callback exactly
`https://127.0.0.1:8182` (no trailing slash). From the repository root:

```sh
source .venv/bin/activate
python -m qte.brokers.schwab_auth login
```

Enter **App Key** and **App Secret** in the hidden terminal prompts, never in
chat, command-line arguments or tracked files. The app secret is not saved.
Sign in on Schwab's website and review its consent page. QTE opens a temporary
HTTPS listener bound only to 127.0.0.1:8182, checks a random OAuth state and
accepts one callback. The browser may warn about the self-signed loopback
certificate. The terminal prints its SHA-256 fingerprint; any exception must be
limited to that exact local URL. Never bypass certificate checks on Schwab's
website. No certificate is installed into the OS trust store.

Check the terminal for successful authorization; the callback page alone does
not prove the token exchange succeeded. Listener and temporary certificate files
are removed when login ends. The callback waits five minutes. Failed exchanges
preserve any previous token. Only one Schwab command can run at a time.

Tokens are plaintext secrets in ignored `.cache/schwab/token.json`, protected
with owner-only POSIX permissions (directory 0700, file 0600), atomic replacement
and symlink checks. Do not share that directory. This first CLI version explicitly
fails on Windows pending Windows credential ACL support; the Windows GUI is
unchanged. Uses the standard library and already-installed OpenSSL; no installs.

```sh
python -m qte.brokers.schwab_auth status
python -m qte.brokers.schwab_auth refresh
```

`status` inspects local expiry metadata only, not provider validity. `refresh`
prompts for the same app credentials and exchanges the refresh token explicitly.
A rotated refresh token is saved only on success; an omitted refresh token
preserves the existing one. Expired/revoked consent requires `login` again.
No background refresh, account reads or trading are enabled.

For market-data reads, supply the reviewed Market Data endpoint file and use its
operation ID and documented parameters. See:

```sh
python -m qte.brokers.schwab_auth read --help
```

`read` accepts `--contract`, `--operation`, `--query` (JSON) and
`--path-parameters` (JSON), and writes raw market-data JSON to stdout. It rejects
trading/account contracts and expired access tokens and reuses the existing
contract-verified GET client. OAuth grants are the only new POST operations,
pinned to Schwab's token endpoint. The broker client remains GET-only. Provider
token scopes may be broader depending on the app; QTE does not reduce those scopes.

Add `--output build/schwab/<new-file>.json` to save a research capture. Create
the output directory first. The capture includes the contract hash, operation
and market-data parameters, UTC request and receipt times, provider JSON and
its canonical JSON hash. It excludes request headers. A capture never replaces
an existing file. The receipt timestamp describes this request, so it cannot
stand in for the historical availability of every observation in a response.

The first authenticated pilot should use a narrow SPY minute-history request
and a small near-weekly option quote or chain request, using the operation IDs
and parameter names in the verified specification. Check returned sessions,
timestamps, contract identity, bid/ask and whether the response is a snapshot.
Minute candles support signal testing. A quote snapshot can start forward
collection. Five-minute options execution validation needs synchronized quotes
across many sessions.

Auth protocol cross-checked against the
[schwab-py maintainers' implementation](https://github.com/alexgolec/schwab-py/blob/master/schwab/auth.py)
and [local flow documentation](https://schwab-py.readthedocs.io/en/stable/auth.html).
No schwab-py dependency was added. Local TLS was tested with a fake grant.
User authorization and authenticated GETs for minute history and option chains
were verified on November 21. See the [Schwab capture checkpoint](tick-data-acquisition.md#first-authenticated-schwab-capture).

## Safety and readiness

### Local GUI: offline prerequisite inspection (G9a)

Open **System → Connections**. The page shows fixed Schwab and Robinhood Crypto
entries; expand one for prerequisites and the existing adapter's scope. Opening
or refreshing this view performs only a local status request. There are no
credential inputs, connect/read/trade buttons, provider calls, automatic polls,
contract-file scans, signer imports or account-response storage.

The server enumerates environment **names only** and checks for
`SCHWAB_ACCESS_TOKEN` and `ROBINHOOD_CRYPTO_API_KEY`; it does not fetch values or
return any other environment names. **Key present** does not
mean nonempty, valid, unexpired, authenticated or authorized: values are not
inspected. Both providers stay blocked even if their keys exist. The GUI has no
mechanism to register a Schwab contract or Robinhood signer yet. No arbitrary
contract path, URL, method, credential or enable flag can be supplied by a page.

`GET /api/v1/connections` is session/token-protected and `Cache-Control: no-store`.
It returns `mode: offline_prerequisites_only`, `read_enabled: false`,
`private_response_retention: false` and two fixed prerequisite records. It is
not a connectivity probe. The defensive `POST /api/v1/connections/{id}/read`
accepts only schema version 1 plus a bounded operation name, then **always
rejects valid requests** with `409 broker_reads_disabled`. Unsupported inputs
are rejected before this denial. This endpoint cannot invoke a broker client.
The Python adapters described above remain separate trusted-code interfaces;
GUI status does not enable or disable their direct use by a caller.

### Deferred: approved private reads (G9b)

Before adding executable GUI reads, decide the first permitted operation and
its bounded redacted response schema, verified contract registration, approved
signer/credential provisioning, approval lifetime, and retry/idempotency policy.
Default response retention should remain off; never register account payloads
as research artifacts. Then implement and mock-test the integration separately.
Real provider verification requires explicit online authorization. G9a does not
complete that gate, validate current provider contracts, implement GUI OAuth, or
establish paper/live trading readiness.

### Existing Python transport boundary

The shared transport is GET-only, bounded, HTTPS-only, and rejects redirects.
Failures omit response bodies and credentials. It neither retries financial
mutations nor exposes a generic POST interface. An injected transport is trusted
application code and must preserve the same destination/security policy.

Credentials are supplied locally, not as committed configuration or command-line
arguments. Do not paste secrets into chat. Provider responses may contain private
account data: do not write them to public research artifacts. Never interpret a
successful read as validation of live-trading readiness.

Remaining prerequisites are provider authorization and credentials, an approved
Robinhood signer or verified Schwab contract, and explicitly authorized read-only
integration checks. Broker-specific normalization and durable ledger
reconciliation require separate milestones. Order submission and strategy-to-
account routing remain disabled pending the paper-trading safety roadmap.
