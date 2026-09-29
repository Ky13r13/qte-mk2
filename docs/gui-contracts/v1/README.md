# Proposed G1 web contract fixtures

These are versioned interface examples prepared during G0, not a server or a test
result. [The preflight](../../gui-g0-preflight.md) defines their route/authentication
context. Future G1 tests should consume these examples rather than duplicate a
second contract. No JSON Schema validator or web dependencies are installed.

`manifest.json` explicitly records `not_implemented` and `proposed_contract`.
Every wire-envelope example has integer `schema_version: 1`. No schema-version
coercion or unrecognized request field is permitted. Runtime response schemas are
to be implemented and validated in G1, not asserted complete by these examples.

- `capabilities-current.json` describes the current GUI state: nothing available.
- `capabilities-expected-g1.json` describes **only a future acceptance scenario**
  with a validated configured document source. Otherwise library state is blocked.
- `session-request.json` and `session-response.json` contain inert placeholders,
  never real authentication material.
- `library-response.json` and `document-response.json` refer to the actual small
  `document-source.md` fixture and its SHA-256. The HTML is a specified rendering
  example, not output from an installed Markdown renderer. Stable heading policy
  uses a registered document ID plus a heading ordinal.
- `error-response.json` uses a stable sanitized error shape.
- `acceptance-cases.json` preserves raw malformed/duplicate-key JSON and duplicate
  header pairs. All unrelated request conditions are assumed valid for each case;
  construct and isolate that setup in G1 tests. Its 45 cases are **not executed**.

For G1, cap an individual Markdown source at 1 MiB before parsing; invalid UTF-8
is a 422 error, oversized source a 413, and disallowed symlinks a sanitized 403.
Revalidate root/file containment when reading. Exact source hashes do not mean
the document's claims are true. Registry implementation statuses are curated.

Browser checks remain necessary for native keyboard behavior, visible focus,
200% zoom, narrow screens, radius zero, no remote media and real cookie/CSP/Fetch
Metadata behavior. HTTP-level test fixtures cannot replace those checks.
