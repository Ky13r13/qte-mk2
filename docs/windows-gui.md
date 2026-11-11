# Native Windows GUI portability

W1 is implemented for the authenticated **Library** and offline **Connections**
pages. It is not full Windows parity. Linux regression and simulated Windows
policy tests run here; native Windows build/browser/handle acceptance is pending.

- Local login/logout and document listing/search/rendering: implemented; native
  acceptance pending.
- Connections prerequisite inspection: offline only; no broker/account requests.
- Gamma Lab calculator: platform-independent manual Black-76 surface; no live
  feed. Limited-host route tests pass on Linux; native browser acceptance pending.
- Research/Data artifacts, forms and jobs: blocked until the storage backend is
  ported.
- Tests/benchmarks/cancellation/restart recovery: blocked until Windows process
  supervision is implemented.

Blocked links do not expose launch controls; direct API requests still require
authentication and receive a sanitized `platform_unavailable` response. The
existing Linux workflows are unchanged.

## Start from PowerShell

Use a Windows-created `.venv` and Windows-built QTE extension in a normal local
drive checkout. **Do not copy a Linux `.venv`, compiled extension or CMake build
directory.** They are not portable binaries. If these already exist, preserve
them separately rather than overwriting/deleting work. The initial reader rejects
UNC/network/device roots, junctions, symlinks and cloud reparse points, including
in checkout ancestors; use a plain local folder instead of a redirected folder.

If your Windows environment already imported QTE and failed specifically on
`fcntl`, update the source using your own Git workflow, then run from that checkout:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m qte gui
```

The offline editable rebuild requires the existing pinned build tools and a native
C++20 toolchain (for MSVC, use its developer PowerShell). It installs no additional
dependencies. If a prerequisite is missing, stop and report the error; this guide
does not install system tools or change PowerShell execution policy.
Alternatively, `scripts/start-gui.ps1` launches the existing environment and
accepts `-Port 8767`; it never installs anything. Direct Python invocation above
also works when local script execution policy blocks `.ps1` files.

Open the printed `127.0.0.1` URL and enter the terminal code. Keep the process open.
System opens offline Connections on this limited host. Research/Data are hidden;
old deep links show the capability limitation. The browser remains local-only,
with the existing Host/Origin/token checks, CSP and one-time login.

Fresh Windows dependency/bootstrap installation is not validated here. Existing
GUI locks were resolved on Linux; platform-conditional dependencies must be
reviewed/pinned before a reproducible Windows bootstrap is claimed. No dependency
download, Windows runtime or cross-compiler was installed during this work.

## Safety boundary

Windows document reads use Win32 handles, not a `resolve()` plus ordinary-open
fallback. Every ancestor is opened/checked with reparse-point traversal disabled;
handles deny write/delete sharing while reading. Descendants are anchored to the
opened volume-GUID path, not a mutable drive mapping. The leaf must be a disk file,
not a directory/reparse point; size is bounded and identity checked before/after.
All handles close on success/failure. Conflicting writers make the read fail
closed. Logical paths reject Windows aliases, alternate streams and device names.
This is only the document-reader boundary, not a port of artifact persistence.

## Native acceptance and remaining work

From the existing Windows venv:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/python/test_gui_windows.py tests/python/test_gui_server.py tests/python/test_gui_windows_handles.py tests/python/test_gui_windows_artifacts.py tests/python/test_gui_disclosure_platform.py -q --basetemp=build/test-windows-acceptance
```

The eight native-only tests check document reads, junction rejection, denied
ancestor write sharing, full-width file identity, artifact inventory/reads and
replacement detection, and bounded disclosure-record validation. They are
intentionally skipped on Linux; mocked tests do not establish Windows security
behavior. This is the W1/W2b/W2c.3 read-side acceptance subset,
not the full Linux suite (which includes POSIX FIFO/process fixtures).
Also check Unicode checkout paths,
concurrent file replacement/sharing, login/logout, document search, Connections,
and blocked job deep links in a real Windows browser. Report failures before
using the Windows build for research.

- **W1 acceptance:** native compilation/imports, PowerShell launch, handle/race
  semantics and browser smoke; no new runtime dependency.
- **W2:** port artifact inventory/stable reads, protected disclosure journaling,
  SQLite storage checks and immutable writes together. Only then enable catalog,
  historical results and Data browsing on Windows.
- **W3:** Windows Job Objects/locking, owner-death cleanup, cancellation/timeouts,
  executable/environment validation and restart recovery; then enable research,
  validation, test and benchmark jobs. Keep Linux and Windows regression gates.

No broker transport, downloads or account trading is enabled by this port.

### W2 implementation sequence

- W2a (implemented and Linux-regression-tested) isolates the artifact filesystem
  backend and shares snapshot
  identity checks between stable reads and catalog caching. Unsupported hosts
  fail before artifact I/O; this foundation does not enable Windows artifacts.
- W2b implements private Windows inventory/stable reads with full-width file
  identity and bounded handle ownership; native acceptance is pending. It is not
  selected by the public storage facade or exposed through GUI artifact routes.
- W2c.1/.2 implement escaped read-only SQLite URIs, connection ownership and
  fail-closed Linux journal publication. W2c.3 adds bounded Windows journal
  reads; native durable writes and SQLite directory/sidecar containment remain.
  Catalog inspection can write protection records, so it is not a read-only
  shortcut.
- W2d integrates and tests those boundaries together before enabling historical
  artifact/Data views. Job launch still requires W3.

W1 native acceptance can proceed independently; Linux tests cannot substitute
for the Windows handle, replacement and durability checks.

### W2c persistence contract

The first W2c increments fix shared persistence behavior without changing the
Windows capability gate:

- W2c.3 validates existing Windows disclosure/protection records through the
  private handle-based reader with the same strict schema and 4096-byte limit.
  It loads no POSIX reader on Windows. Non-Linux journal writes, including empty
  requests, explicitly fail before filesystem access; reading history does not
  imply permission or capability to persist a new disclosure.
- Read-only SQLite names must be escaped file URIs, including spaces, Unicode,
  `%`, `#` and `?`. Resolve them lexically to absolute paths, not through symlinks.
  Keep `mode=ro`; do not use `immutable=1` on a database shared with jobs.
- Each SQLite operation owns its connection until commit/rollback and closes it
  on success or failure. A connection's transaction context alone does not close
  it. Return materialized rows, not a live cursor outside the scope.
- Keep schema-v1 immutable JSON disclosure/protection records outside SQLite.
  Rebuilding the catalog must not erase this evidence. Validate serialized size
  before creating files; accept an existing record only if its schema, event and
  identity match. Never overwrite an invalid collision. Clean up only temporary
  files created by the current operation; report publication failures.
- Publication is per record, not an atomic batch. A failure after publication
  may leave valid evidence; never roll back a published disclosure. Successful
  Linux publication requires file and directory flushes. Fault-injection tests
  check error handling, not power-loss durability on other filesystems.

These fixes do **not** make SQLite pathname opens race-safe on Windows. Before
W2d, choose and validate a native containment mechanism covering ancestors, the
database and all journal/WAL/SHM sidecars for the **whole connection lifetime**,
without breaking SQLite's locking or recovery. Prechecking reparse points and
passing an escaped URI is insufficient. A custom VFS is a possible fallback,
not an approved dependency or implementation decision.

The Windows immutable writer also needs verified create-new publication,
handle-based collision checks, cleanup, flushing and crash/restart semantics.
Do not assume a rename or `FlushFileBuffers` alone proves directory-entry
durability. Native sharing/reparse/replacement and crash-recovery tests are
required before enabling artifact views. The recommended default remains the
existing journal format and a closed capability gate until these checks pass.

The W2b reader uses tagged 64-bit volume/128-bit file IDs and raw Windows
last-write/change times, not fabricated POSIX inode/ctime values. It enumerates
names under pinned volume-GUID directories, checks every entry through a handle,
and rejects ambiguous case-folded names and reparse points. Limits are 10,000
entries, 256 MiB total inventory, 16 MiB per read and 128 pinned handles including
checkout ancestors; each active directory also owns one bounded enumeration
iterator. Concurrent changes fail validation; this is not an atomic filesystem
snapshot. Unsupported identity queries fail without a weaker fallback.
