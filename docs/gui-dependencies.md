# GUI dependency proposal — 2026-09-23, post-boot

Status update, 2026-09-24: **approved and installed** in the existing `.venv`.
The user approved the pinned packages and Sol/Terra build agents with Astra
review. Hash-checked, wheel-only installation succeeded; imports and `pip check`
passed. G1 implementation is under review. The proposal and original checks
below remain a historical record, not the current approval state.

## Environment recheck

The existing `.venv` now imports `ssl` successfully and reports OpenSSL 3.5.8.
`rpm -V --nouser --nogroup python3-libs.x86_64 openssl-libs.x86_64` exits zero
with no findings. This clears the previously observed blocker for this check;
it does not establish why the earlier extension mismatch occurred or disappeared.
No repair, system change or venv replacement was performed by this task.

The initial post-boot package dry-run hit restricted DNS/network access, distinct
from the old SSL problem. After approval for network access, a binary-only pip
dry-run resolved the packages below. **Network/dry-run approval did not authorize
installation**, and all 12 packages are still absent from the venv.

## Exact proposal

| Scope | Direct packages | Transitive packages |
|---|---|---|
| Optional GUI runtime | starlette 1.7.0; uvicorn 0.53.0; markdown-it-py 4.2.0 | anyio 4.15.1; click 8.5.0; h11 0.16.0; idna 3.20; mdurl 0.1.2; typing-extensions 4.16.0 |
| Optional GUI tests | httpx 0.28.1 | certifi 2026.7.22; httpcore 1.0.9; reuses runtime dependencies above |

- [Runtime input](../requirements-gui.in) and [runtime lock](../requirements-gui.lock).
- [Test input](../requirements-gui-test.in) and [test lock](../requirements-gui-test.lock),
  which includes the runtime lock. Combined closure: 12 packages.
- Pins and SHA-256 hashes match the resolver's selected wheel metadata. The
  `Requires-Python` declarations and active dependency constraints were checked
  for CPython 3.14.7 on Linux x86_64. This is **metadata compatibility**, not an
  import/runtime/security test or a promise for other environments.
- Hashes are package-index declarations at this stage; a later approved install
  must verify the downloaded bytes with `--require-hashes`. No source builds,
  compiler/runtime installs, Node tooling or `full`/`standard` extras are proposed.
- No GUI extra was activated in `pyproject.toml` before dependency approval.
  Declare that optional extra during G1; ordinary engine imports must not load it.

Use HTTPX's direct `ASGITransport`/`AsyncClient` with explicit startup/shutdown
fixtures, rather than relying on Starlette TestClient's deprecated plain-HTTPX
compatibility path. That avoids an unplanned HTTPX2 or `starlette[full]` addition.
The existing pytest plus `asyncio.run` suffices for the proposed basic HTTP tests;
no new async pytest plugin is proposed. These tests still cannot establish browser
cookie/CSP/fetch-metadata behavior; browser acceptance remains separately required.
See the primary [Starlette TestClient documentation](https://starlette.dev/testclient/)
and [HTTPX transport documentation](https://www.python-httpx.org/advanced/transports/).

## Commands

Successful dry-run, from repository root, with approved network access:

```sh
PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TMPDIR="$PWD/build" .venv/bin/python -m pip install --dry-run --ignore-installed --only-binary=:all: --retries 0 --report build/gui-dependency-proposal-20260923.json starlette uvicorn markdown-it-py httpx
```

The ignored report is local resolver evidence, not a research result. The checked-in
locks contain the selected pins and hashes so the proposal survives cache cleanup.

Approved installation completed with this command (plus `--retries 0`):

```sh
PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 TMPDIR="$PWD/build" .venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements-gui-test.lock
```

After approval, verify imports, `pip check`, Markdown security configuration and
HTTP/server lifecycle behavior before claiming compatibility. Installing these
packages does not itself create a GUI or authorize broker requests.

## Contracts and baseline validation

[Versioned contract examples](gui-contracts/v1/README.md) contain nine JSON
documents, a tiny source Markdown file and 45 proposed acceptance cases. Parsing,
unique keys/IDs, source digest, linked summaries, example coverage and capability
labels were checked with the existing venv standard library. These are proposed
payloads and future HTTP tests, **not a working API or 45 passing GUI tests**.

Fresh post-boot regression commands:

```sh
PATH="$PWD/.venv/bin:$PATH" cmake --build --preset dev
PATH="$PWD/.venv/bin:$PATH" ctest --preset dev --output-on-failure
.venv/bin/python -m pytest tests/python -q --basetemp=build/test-gui-resume-20260923
git diff --check
```

Build succeeded (no work required), **15/15 CTest executables passed** and
**419 Python tests passed in 9.42 seconds**. No existing tests were modified.

## Original approval gate (subsequently approved)

Approve the exact optional dependency installation above. Separately, the requested
GPT-6 Sol/Luna build models are still absent from this session's callable agent
tool; approve GPT-5.6 Sol/Luna substitutions under GPT-6 Astra review, or wait for
the requested versions. No substitute build agents have been started. G0 remains
approval-pending, not complete; G1 implementation follows only after these gates.

GPT-6 Astra independently checked all 12 proposed versions/hashes against the
resolver report and reviewed the fixtures/approval boundaries, finding no blockers
to presenting this proposal. Its request for explicit logout/restart invalidation
cases was incorporated. This review is not a runtime compatibility certification.
