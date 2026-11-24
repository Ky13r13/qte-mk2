# QTE development rules

## Architecture

- Keep the C++20 core deterministic, modular, provider-independent, and free of
  broker-specific assumptions.
- Translate external data at adapter boundaries. Core code consumes only
  canonical QTE types; raw payloads and vendor schemas stay in adapters.
- Keep mutable state ownership explicit. Portfolio accounting will have one
  authority, and strategies will express intent by submitting orders.
- Enforce information availability and deterministic event ordering to prevent
  look-ahead bias. Document execution, cost, timestamp, currency, adjustment,
  and missing-data assumptions.
- Prefer small concrete interfaces. Add abstractions when an implemented second
  use case needs them.

## Development and testing

- Work in small milestones: architecture, skeleton, market data, orders/fills,
  execution, accounting, strategy API, event loop, analytics, then adapters.
- Add meaningful deterministic tests for each behavior and regression tests for
  every accounting defect. Investigate failures; never weaken or delete a test
  to accommodate broken behavior.
- Use C++20, RAII, const correctness, explicit ownership, minimal global state,
  and compiler warnings. Check financial invariants and Python binding lifetimes.
- Compile and run the relevant C++ and Python tests before reporting completion.
  Report tests that could not run and the exact blocker.

## Dependencies, tools, and safety

- Inspect existing tools before adding dependencies. Prefer the system C++
  compiler and repository-local Python packages in `.venv`.
- Do not install compilers, runtimes, package managers, build systems, or system
  packages without explicit user approval. Do not use `sudo` or modify files
  outside this repository without explicit approval.
- Preserve user work and avoid destructive Git commands. Do not replace,
  reinitialize, or delete `.git` without explicit approval.
- Keep caches and generated build artifacts inside ignored repository paths.
- Never delete files or directories under `build/`, `data/` or `.cache/`
  (artifacts, captures, databases, test evidence) without asking the user first and
  getting an explicit yes for the specific items. Moving or copying is fine when asked.
- Do not modify unrelated code, add speculative features, or expose internal C++
  details through Python without a concrete API need.
  
## Git policy

- Do not commit, push, pull, merge, rebase, reset, or modify remotes unless explicitly requested.
- The user owns all Git history and remote operations.
- Codex may inspect `git status`, `git diff`, and `git log`.
- Never discard uncommitted user changes.

## Current capabilities (read first)

- Start every session by reading `START-HERE.md` (the one goal and the five commands), then `docs/README.md` (capability map, path-to-live
  status, which docs are current vs historical) and `CONTINUITY.md` (latest
  handoff and results). Do not rebuild something listed there; extend it.
- Before building an engine or subsystem, search `include/`, `src/` and
  `python/qte/` for an existing implementation (Codex and Claude have both built
  large parts). Prefer wiring and extending over duplicating.
- Numeric engines (backtests, sweeps, signal evaluation at scale) are C++ in
  `qte_core` with pybind11 bindings. Keep a Python reference implementation and
  parity tests whenever behavior must match research code.
- The portfolio ledger/`BacktestEngine` is the accounting authority. The research
  sweep kernel (`include/qte/research/sweep.hpp`) is screening only.

## Data, search and live-trading safety

- Market data enters research through the truth database (`qte truth`, stored in
  `data/truth/`: database, raw captures, contracts; rebuildable from captures). Never
  overwrite final bars; conflicts are logged. Never ingest test, fixture, demo
  or synthetic files into it.
- Schwab intraday history rolls off (1m ~48 days, 5m ~9 months): remind the user
  to refresh and run `qte truth collect` at least weekly.
- Strategy search: dataset splits in `build/search/splits/` are locked once
  written. Agents and AI sessions must never run `qte search holdout`, edit or
  delete a split, or delete trials. Every evaluated spec counts as a trial.
  Report deflated probabilities and dev-vs-validation consistency, not just the
  best validation Sharpe.
- No code may transmit orders until the user explicitly approves the order
  gateway milestone (see the path to live in `docs/README.md`).
- Credentials: Schwab token in `.cache/schwab/`, Alpaca keys in
  `~/.config/qte/alpaca.env` (outside the repo). Load them only inside the
  command that needs them; never print, log, commit or paste them.

## Documentation maintenance

- Every change that adds, removes or changes a capability updates, in the same
  change: `docs/README.md` (capability map/status), the relevant current
  reference doc, and `CONTINUITY.md` (concise; replace stale lines rather than
  appending forever).
- Dated research records and milestone records are history: do not rewrite
  them; add a new dated record. If a record is superseded, add a one-line
  `> **Status:**` banner pointing to the current doc.
- Never leave a statement that contradicts current behavior in a current
  reference doc (e.g. "pending", "unsupported", counts of tests). Verify claims
  against code or command output before writing them.
- New docs must be linked from `docs/README.md`.
