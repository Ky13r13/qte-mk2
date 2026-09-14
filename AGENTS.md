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
- Do not modify unrelated code, add speculative features, or expose internal C++
  details through Python without a concrete API need.
  
## Git policy

- Do not commit, push, pull, merge, rebase, reset, or modify remotes unless explicitly requested.
- The user owns all Git history and remote operations.
- Codex may inspect `git status`, `git diff`, and `git log`.
- Never discard uncommitted user changes.
