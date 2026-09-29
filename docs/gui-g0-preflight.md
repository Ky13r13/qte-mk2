# GUI G0 preflight and first-screen contract — 2026-09-23

Status after reboot: **dependency/contract proposal prepared; approval pending**.
This continues the [GUI architecture](gui-architecture.md), not a working GUI.
No engine, bindings, runtime code, installed packages or system files changed.
The [post-boot dependency proposal](gui-dependencies.md) records successful SSL
and RPM rechecks, exact uninstalled locks, versioned examples and fresh tests.
The earlier failure below is retained as history, not the current blocker.

## Historical environment blocker (before reboot)

At the failed preflight, the repository `.venv` used `/usr/bin/python3.14`, version 3.14.7.
Starlette, Uvicorn, markdown-it-py and HTTPX are not installed in it. A binary-only
pip **dry-run**, with cache disabled and temporary files under `build/`, could
not resolve their versions because Python could not import its native SSL module.
That failed attempt produced no dependency lock or resolver report.

Both `.venv/bin/python` and `/usr/bin/python3.14` raised:

```text
ImportError: /usr/lib64/python3.14/lib-dynload/_ssl.cpython-314-x86_64-linux-gnu.so:
undefined symbol: SSL_CTX_set_keylog_caLlback, version OPENSSL_3.0.0
```

Read-only ELF inspection shows the extension requires the spelling `caLlback`
but `/lib64/libssl.so.3` exports `callback`. Package owners are
`python3-libs-3.14.7-1.fc44.x86_64` and
`openssl-libs-3.5.8-1.fc44.x86_64`. Targeted RPM verification reports:

```text
..5......    /usr/lib64/python3.14/lib-dynload/_ssl.cpython-314-x86_64-linux-gnu.so
```

The file then differed from its recorded package digest. This established the failing
symbol and a local file-integrity mismatch, **not why or when it changed**.
It is not an absence of Starlette on the package index, a certificate-trust error,
or a reason to use HTTP, `--trusted-host`, patched binaries, a replacement runtime
or a new venv. The existing venv uses the affected system module.

System repair is outside the repository and current permission scope. Do not
modify/reinstall system Python or OpenSSL, use sudo, replace the venv or bypass
TLS. Obtain user direction first. After repair, recheck `import ssl` in the venv,
repeat the dependency dry-run, review the exact optional dependency lock, then
obtain installation approval. A successful dry-run still does not prove runtime
compatibility; G1 tests must do that after an approved installation.

## G1a/G1b contract prepared with GPT-6 Astra

This is the bounded first implementation target; jobs, artifacts, accounts and
research execution are deliberately absent. [Versioned G0 examples](gui-contracts/v1/README.md)
now record proposed payloads and acceptance cases, not implemented/tested routes.

| Route | Behavior |
|---|---|
| `GET /` | Redirect to `/library`; unauthenticated shell requests redirect to login |
| `GET /login`, `GET /assets/{name}` | Inert login and fixed packaged assets only; no directory mount |
| `POST /api/v1/session` | Exact-origin one-time-code login, maximum 4 KiB JSON body |
| `GET /api/v1/session` | Authenticated same-origin token bootstrap after page reload |
| `DELETE /api/v1/session` | Exact-origin logout with session request token |
| `GET /library`, `/library/{id}` | Authenticated library shell/detail |
| `GET /research`, `/data`, `/system` | Shell with honest unavailable state; no functional controls |
| `GET /api/v1/capabilities` | Library available after its configured document source validates; otherwise blocked; other features unavailable |
| `GET /api/v1/library`, `/api/v1/documents/{id}` | Approved document search and safe rendered content |

Unknown routes return 404 and wrong methods 405. Every JSON envelope includes
`schema_version: 1`; request objects reject unknown fields and duplicate keys.
The login request contains only `schema_version` and `code`. Minimal responses:

- Session: request token and idle timeout; never the cookie's secret value.
- Capabilities: IDs, `available | planned | blocked` states, explanatory reasons.
  Include library, research, data, test execution, broker reads and paper execution.
- Document summary: opaque stable ID, title, category, repo-relative source path,
  source SHA-256 and curated `implemented | proposed | mixed | unknown` status.
- Library: summaries, total, offset and limit. Literal case-insensitive search
  (`q` at most 200 characters), deterministic category/title/ID ordering,
  default 25/max 200 results. Search approved documents only in G1.
- Document: summary, safe server-rendered HTML and prefixed heading IDs/text/level.
  Hash the same source bytes that are rendered; disable raw HTML and remote media.
- Error: stable code, sanitized message and request ID, without traceback/secrets.

Use an explicit source-checkout document manifest; no arbitrary repository scan,
absolute input path or dynamic import. Resolve only registered internal links.
Missing/disallowed links remain visibly unavailable. Without an explicitly
configured checkout containing registered docs, report “library source unavailable”;
do not search parent directories or claim wheel-bundled documentation exists.

Authentication defaults: one active in-memory session, 30-minute idle expiry,
five failed logins per minute; startup/replacement codes issued only to the local
terminal. Codes are unpredictable and one-use, session and request tokens are
independent, restart invalidates them. Session cookie is host-only, HttpOnly,
SameSite=Strict, Path=/; JSON/session responses are no-store. Token stays in browser
memory, never a URL, persistent browser store or log. Logout/expiry emits a single
new terminal code, not a new code for every rejected request.

Require exact Host/port, reject wrong/null Origin or cross-site fetch metadata,
disable proxy-header trust. Mutations require exact Origin and a request token
(login instead presents its one-time code). Token bootstrap requires a valid
session and `Sec-Fetch-Site: same-origin`; missing metadata fails explicitly.
Other API reads require the session and token. Handle shell navigation separately
so normal browser navigation does not expose sensitive JSON or create a circular
“token required to retrieve token” protocol.

Acceptance fixtures must cover login/replay/throttle/expiry/restart, duplicate
Host and wrong Origin, valid reload bootstrap, missing/mismatched tokens,
script/link/image injection, internal-link containment, encoded traversal,
symlink files/parents, oversized/invalid-UTF-8 documents, stable headings/search,
honest capability states, zero-radius keyboard/zoom layouts, and absence of
subprocess/provider calls during navigation. These are requirements, not claims
of an implemented or tested web server.

## Commands and results

Run from `/home/glsu6/Documents/qte`:

```sh
PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TMPDIR="$PWD/build" .venv/bin/python -m pip install --dry-run --ignore-installed --only-binary=:all: --report build/gui-dependency-proposal-20260923.json starlette uvicorn markdown-it-py httpx
.venv/bin/python -c 'import ssl; print(ssl.OPENSSL_VERSION)'
/usr/bin/python3.14 -c 'import ssl; print(ssl.OPENSSL_VERSION)'
ldd /usr/lib64/python3.14/lib-dynload/_ssl.cpython-314-x86_64-linux-gnu.so
readelf --dyn-syms --wide /usr/lib64/python3.14/lib-dynload/_ssl.cpython-314-x86_64-linux-gnu.so | rg 'SSL_CTX_set_keylog'
readelf --dyn-syms --wide /lib64/libssl.so.3 | rg 'SSL_CTX_set_keylog'
rpm -qf /usr/lib64/python3.14/lib-dynload/_ssl.cpython-314-x86_64-linux-gnu.so /lib64/libssl.so.3
rpm -V python3-libs openssl-libs
rpm -V --nouser --nogroup python3-libs openssl-libs | rg '(_ssl|libssl|libcrypto)'
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-gate-20260923
git diff --check
```

The resolver and both SSL imports failed as described. RPM verification initially
also emitted many ownership differences; filtering those out still exposed the
SSL extension digest mismatch. No package verification result justifies automatic
repair. Native build succeeded with no work required; **15/15 CTest executables**
passed, and **419 Python tests passed in 11.77 seconds**. These offline tests do
not establish HTTPS/runtime health. Whitespace checks passed.

## Next approval gates

1. SSL now imports and scoped RPM verification passes; no system repair is currently
   requested or authorized. The earlier mismatch's cause remains unknown.
2. Approval of exact resolved GUI/test dependencies before installation in `.venv`.
3. Requested GPT-6 Sol/Luna are not callable through this session's agent tool.
   GPT-6 Astra remains the architect/reviewer; GPT-5.6 Sol/Luna substitutions still
   require user approval. No substitute build agents were launched.

G0 is approval-pending, not complete; G1 has not started. No sudo, system mutation, package install,
TLS bypass, broker request or Git history operation was performed.
GPT-6 Astra reviewed this preflight and first-screen contract without blocking
findings; the environment/approval gates above are still unresolved.
