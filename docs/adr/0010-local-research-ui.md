# ADR 0010 — Local research library and UI boundary

Status: proposed, 2026-09-22; implementation and new dependencies require approval.

## Context

QTE already has deterministic C++/Python research workflows, two saved artifact
layouts, documentation and tests, but no GUI. The user wants a local, extremely
sparse, minimally colored, square-cornered interface to that work. Broker reads
are offline-tested foundations; paper/live strategy execution is not operational.

## Decision

- Build an optional Python-hosted browser UI: Starlette, base Uvicorn and
  markdown-it-py, plain local JavaScript/CSS/SVG, no frontend build runtime.
  Approve and lock dependencies in `.venv`; headless QTE stays independent.
- Four sections, Library / Research / Data / System. Start with a read-only
  documentation/evidence library. One focused view, neutral palette, radius zero,
  no KPI dashboard. Essential evidence warnings remain visible.
- Keep C++ portfolio/accounting/analytics authoritative. Web handlers consume
  immutable DTOs; simulations run through existing workflows in fresh processes.
  No engine objects, arbitrary strategy code, shell commands or mutable accounts
  exposed to a browser. Exact integer ns/IDs/quantities use decimal strings.
- Explicit artifact-root registry, dedicated legacy readers, checksum/schema/path
  verification, immutable exports. UI SQLite tracks metadata/jobs only. Do not
  scan every build/test directory or mutate original artifacts.
- Holdout disclosure records are append-only outside the rebuildable catalog,
  keyed by stable content/protocol identities. Restrictions apply to every preview
  and download, including mixed-role files; unknown history is never untouched.
- Export integrity, research evidence, scenario outcome, code identity, selection
  and holdout state are separate facts. Never promote synthetic evidence, discard
  failed candidates or reconstruct omitted accounting from CSVs in JavaScript.
- Loopback-only, authenticated local session, host/origin/CSRF checks, escaped
  rendering, constrained file access, no third-party browser assets. Credentials
  stay server-side and never enter research workers/reports.
- One bounded local job at a time, fixed argv, immutable input/config snapshots,
  idempotent submission, explicit cancellation and fail-closed crash recovery.
  Completion requires verified outputs, not just a process exit code.
- Data downloads and broker reads require deliberate actions and their existing
  prerequisites. No trading/paper mutation controls before separate operational
  milestones and explicit authorization.

## Consequences and alternatives

This introduces a few Python web dependencies and small view modules, but avoids
a second toolchain and duplicated financial logic. A full SPA is deferred until
measured UI complexity warrants it. A notebook/dashboard wrapper would be quicker
initially but weakens control over presentation and long-running process state.
A custom standard-library HTTP server saves packages at the cost of hand-built
security/routing; it is not the recommended default.

Legacy files cannot show data they never recorded. Add minimal read-only Python
bindings for currently unexposed owned result fields, test their lifetimes, then
versioned trade/position/order-event/sequence exporters. Preserve old readers and
label absent fields honestly. GUI source changes may alter QTE's existing
package fingerprint; preserve that meaning and record UI assets separately.

The [GUI architecture and rollout](../gui-architecture.md) fixes API, paths,
security, evidence semantics and G0–G9 acceptance tests. This ADR changes neither
the canonical Bar contract nor existing ownership/execution/accounting decisions.
