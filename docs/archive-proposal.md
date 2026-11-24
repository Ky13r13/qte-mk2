# Archive proposal (2026-11-24): nothing has been moved yet

Purpose: shrink what a person or an AI sees day to day without losing anything.
"Archive" means **move** into `archive/` (tracked in git, still searchable). Nothing is
deleted. Each tier is approved separately. After each tier: tests green, links
checked, `docs/README.md` updated.

## Tier 1: historical docs (no code impact, low risk)

Move to `archive/docs/`:
- **Milestone and validation records:**
  - `development-audit.md`
  - `validation-20260922.md`
  - `m13-m16-validation.md`
  - `gui-g0-preflight.md`
  - `gui-restart-handoff.md`
  - `gui-dependencies.md`
- **GUI phase records:**
  - `gui-result-bindings.md`
  - `gui-lab-views.md`
  - `gui-research-forms.md`
  - `gui-jobs.md`
  - `gui-comparisons.md`
  - `gui-test-evidence.md`
  - `gui-benchmarks.md`
  - `gui-export-v2.md`
- **Finished or failed strategy studies** (`docs/strategies/`):
  - S1/S2 `core-reversion`, `s5-oversold-bounce`, `s7-trend-day-convexity`
  - `book151*`, `spx-0dte-timing`, `spxw-*`
  - `weekly-reversal-research`, `reversal-tail-study`, `volume-strategies`
  - `strategy-review`, `nasdaq-gamma-scalping`, `vix-expected-moves`
  - `macro-router`, `candidate-library`, `strategy-lab`, `selection-protocol`
  - `moving-average-reference`, `regime-framework`, `schwab-minute-refinement`
  - `spx-regime-study`, `search-round1-20261124`, `lowvol-call-scalp`
- **Also:** `claude trading research/` (an early spec) moves to `archive/specs/`.

**Stays put:**
- the current reference docs listed in `docs/README.md`,
- the ADRs,
- the condor and regime-lab records,
- `gui-task-log.md`, because the daily agent still appends to it.

**Before moving, check each file:** the GUI Library indexes `docs/`, and some GUI
tests reference specific documents. Any doc a test or the GUI needs stays, or its
reference gets updated in the same change.

## Tier 2: example study scripts (low risk)

Move to `archive/examples/`:
- `examples/book151_*.py`
- `spx_0dte_*.py`
- `daily_regime_year.py`
- `vix_expected_moves.py`
- `nasdaq_gamma_policy.py`

Each is referenced by about one test or doc. Those references move with the file or get removed.

## Tier 3: study code wired into the CLI and GUI (medium risk, one family at a time)

These are one-off studies that are finished or failed. They're still imported by the
CLI, GUI or tests, so archiving one means unhooking its subcommand or route and
moving its tests with it:

| Family | Modules | Wiring |
|---|---|---|
| S1/S2 core reversion | `core_reversion_*`, `core_vertical_replay` | `qte s1-study`, `s2-study` |
| S5 | `s5_*` | `qte s5-study` |
| SPX spread / single / 0DTE studies | `spx_spread_study`, `spx_single_study`, `spx_0dte_*`, `spx_public_data` | tests only |
| Reversal tail / weekly reversal | `reversal_tail*`, `weekly_reversal*` | GUI routes |
| Minute refinement | `minute_refinement*`, `minute_option_scenarios` | tests only |
| Options research | `options_master`, `options_research`, `research_triage`, `regime_report` | GUI |
| Low-vol call scalp (failed) | `lowvol_call_scalp` | tests only |

**Must stay, even when their study is archived**, because active code uses them:
- `regime_study` (used by 8 modules),
- `spx_spreads` (8),
- `s7_tape` and `s7_study` (the signal engine and condor depend on them),
- `volatility`,
- `gamma_surface`,
- `json_input`.

## Tier 4: `build/` (8.5 GB)

Your instruction stands: nothing is deleted. Options for later:
- move old runs (`test-*`, `pytest-*`, `terra-*`, `astra-*`, about 6 GB) into `build/_old/` so the top level is readable, or
- move them off the repo disk.

Either way, first run a filesystem check: two of those folders returned I/O error 5.

## Order I'd suggest

Tier 1, then Tier 2 (documents and examples only, quick, about 40 files). Then Tier 3,
one family per change.
