# Read-only broker connection foundations

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

## Schwab: official contract export required

The public [Schwab Trader API product page](https://developer.schwab.com/products/trader-api--individual)
identifies account/trading and market-data products. Its detailed production
contracts were authentication-gated during inspection. QTE therefore does not
claim independently verified endpoint paths or use a guessed fallback.

`SchwabReadOnlyClient(access_token, openapi=None, transport=None)` requires a
user-supplied official OpenAPI export before requests can execute. Its
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

The caller is responsible for obtaining and verifying that export through
Schwab's official portal; accepting a supplied document does not authenticate
its provenance. The example uses the already-ignored repository `.cache` directory.
Keep the export and credentials local and out of shared reports.
OAuth enrollment, consent, callback handling, refresh, token storage, and account
permissions remain outside this implementation. An expired token requires the
user's existing approved authentication workflow; QTE does not silently refresh
it or persist credentials.

## Safety and readiness

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
