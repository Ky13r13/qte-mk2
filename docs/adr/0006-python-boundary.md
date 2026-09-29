# ADR 0006: C++ ownership and Python research API

Status: Implemented through M10b: C++ strategy lifecycle, callback-scoped
context, owned snapshots, buffered commands, optional pybind11 package,
trampoline/GIL behavior, exception propagation, and reentrancy protection.
G3b adds read-only owned result coverage without changing these ownership rules;
see [result binding fields](../gui-result-bindings.md).

## Decision

Keep engine state in C++: portfolio, order registry, execution state, feed cursor,
clock and result builder are owned by one run. A fresh run resets IDs and state;
same inputs cannot inherit prior positions or strategy indicator history.
Require a fresh strategy instance per run initially. Store no owning raw pointers.

Strategy callbacks use `on_start(ctx)`, `on_bar(ctx, bar)`, `on_fill(ctx, fill)`,
`on_order_update(ctx, update)`, `on_end(ctx)`. Order updates are read-only
notifications; commands remain forbidden there. C++ Strategy is a virtual interface; Python uses a pybind11
trampoline. Context supplies bounded history, marked position/equity snapshots,
and queued submit/cancel commands. A submission returns an order ID immediately,
not an approval/fill promise. Its receipt remains pending until the command phase.
Callbacks cannot retain an unrestricted feed/dataset handle through Context.

Publish bars, positions, fills, orders and result records as Python read-only
value snapshots. C++ Bar stays a value struct as currently implemented. Python
input construction may use a separate value conversion/factory. Returned data
cannot mutate live state. Do not return references into resizable engine vectors.
Retaining a snapshot after engine destruction is safe.

Context is callback-scoped. Exposed handles contain a weak run reference plus a
callback generation token; every method checks liveness, generation and current
callback thread. Expired, cross-thread or on_end mutation calls raise a clear
exception. Context does not keep a run alive. This prevents dangling pointers
even if a Python strategy stores its context accidentally.

The implementation applies this contract with a weak driver-state reference, monotonically
increasing callback generation, and originating-thread check on every context
method. Portfolio and position reads return owned values. The isolated driver
owns its strategy and command buffer, exposes command snapshots by value, and
rejects command draining during callbacks. The isolated driver remains an
acceptance harness; replay uses the same contract through the Python trampoline.

`run` retains strong ownership of the Python strategy for the synchronous call.
Release the GIL around C++ replay; acquire it around each Python callback and
Python object access/destruction. No parallel callbacks, reentrant run, background
run or mutation from another thread initially. A callback exception terminates
the run, invalidates handles and is propagated with original Python traceback;
optional diagnostic records are marked failed and never presented as successful
results. Clean up Python references with the GIL held on every exit path.

Python config normalizes to validated typed C++ configuration before on_start;
reject unknown keys/unsupported values. Version the normalized config in results.
Initial input is owned copied canonical data, not a borrowed NumPy buffer.
Results own their records after run completion and expose conversion helpers;
pandas/plotting remain optional research dependencies. Time conversion preserves
int64 nanoseconds explicitly; Python datetime's precision must not silently
truncate timestamps. Domain validation exceptions map to ValueError-derived
exceptions, liveness/reentrancy errors to RuntimeError-derived exceptions.

## Package structure

Use existing CMake target with PIC when linking a pybind11 extension. Add a
`bindings/` translation unit, `python/qte/` facade, `tests/python/`, and a
scikit-build-core `pyproject.toml`. Extension name is `qte._core`; facade exposes
stable concepts, not compiler-specific implementation details. CMake binding
support is optional so C++ tests never require Python. Use ordinary CPython ABI
wheels; no promise of stable-ABI/abi3 or cross-compiler C++ ABI compatibility.
Existing `.venv`, pinned tools and native GCC remain the development toolchain.

## Acceptance / consequences

Test a Python strategy through all lifecycle callbacks, next-open order routing,
retained value snapshots after destruction, expired Context, callback exceptions,
reentrant run rejection, invalid configuration, exact nanosecond round-trip and
identical C++/Python strategy results on a fixed fixture. Test fresh strategy
instances across repeated runs. Inputs changing in Python after conversion must
not change the run's owned data.

Copying snapshots/input costs memory but makes lifetimes auditable. Zero-copy
views and batched Python callbacks need benchmarks and explicit ownership tests
before adoption. Python code can deliberately consult external future data;
the guarantee covers engine-provided visibility, not sandboxing arbitrary user
strategy code. Document external dependencies when assessing reproducibility.
