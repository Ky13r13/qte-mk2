# GUI restart handoff — 2026-09-23

The user is rebooting. Work is saved in `/home/glsu6/Documents/qte`.
Read `AGENTS.md` before resuming. Preserve all modified/untracked work; saving
does not mean committing. No Git history or remote operations are authorized.

**Post-boot update:** SSL import and scoped RPM verification now pass. The
[dependency proposal](gui-dependencies.md) contains exact uninstalled pins/hashes
and [versioned contracts](gui-contracts/v1/README.md). G0 now waits for installation
and build-model approval, not a currently failing SSL import. No repair was
performed by this task; the reason for the earlier mismatch remains unknown.

## Saved state

- [GUI architecture](gui-architecture.md): minimalist local research/documentation
  library, neutral colors, zero rounded corners, four sections; G0–G9 milestones.
- [ADR 0010](adr/0010-local-research-ui.md): UI/core ownership and safety boundary.
- [G0 preflight](gui-g0-preflight.md): first-screen API/authentication contract,
  exact historical diagnostics, tests and approval gates. GPT-6 Astra reviewed both the
  architecture and preflight without remaining blocking design findings.
- README, architecture index and roadmap link the plans. **No GUI implementation
  or GUI dependencies have been installed. G0 is incomplete; G1 has not started.**

## Recheck after boot

From the repository root, use the existing venv:

```sh
.venv/bin/python -c 'import ssl; print(ssl.OPENSSL_VERSION)'
```

Before reboot, both venv and system Python failed this import. The native `_ssl`
extension required `SSL_CTX_set_keylog_caLlback` (capital L), while OpenSSL
exported `SSL_CTX_set_keylog_callback`. RPM verification reported a digest
mismatch for `/usr/lib64/python3.14/lib-dynload/_ssl.cpython-314-x86_64-linux-gnu.so`.
The cause is unknown. **Do not assume reboot repaired an on-disk mismatch.**

If the check still fails, report the actual error and ask for user direction.
No system repair, sudo, replacement venv, TLS bypass or outside-repository
mutation is authorized. If it succeeds, resume G0 dependency resolution with
cache disabled and repository-local temporary files, then seek approval for the
exact dependency lock before installing. Proposed direct packages: Starlette,
base Uvicorn, markdown-it-py; HTTPX for HTTP tests. The previous pip dry-run failed
before producing a report or lock and installed nothing.

## Verification and preserved research

Latest post-boot verification: native build succeeded, **15/15 CTest executables**
passed and **419 Python tests passed (9.42 seconds)**. These are offline baseline
tests, not evidence that HTTPS or an unimplemented GUI works. Exact commands are
in the preflight record. Use `build/local-dev`, never stale `build/dev`.

Do not overwrite existing research exports, including
`build/strategy-lab-20260922` (final synthetic reference),
`build/strategy-lab-20260920` (older slate), and
`build/trend-reference-20260921`. Synthetic outcomes establish mechanics, not
profitability. No account access or trading has occurred in this GUI work.

## Delegation and approvals

User requested GPT-6 Astra architecture/review and GPT-6 Sol/Luna build agents.
At handoff, the agent tool exposes Astra but only GPT-5.6 Sol/Luna build variants.
Recheck available model IDs after reboot; do not silently substitute versions.
Substitution approval is still pending. Do not assume agents or running processes
survive a restart. No GUI server or background job was started.

Resume prompt: “Read docs/gui-restart-handoff.md and docs/gui-dependencies.md.
Continue G0/G1 only after the dependency-install and build-model approvals are
resolved; do not repair system files or silently substitute models.”
