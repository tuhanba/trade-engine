# Professionalize & Stabilize the Trade Engine — Spec

> Status: Draft · Date: 2026-06-18

## Objective
Bring the existing AurvexAI trade engine to a professional, reliably-running
baseline. Today the repository ships a large codebase (engine + Flask/SocketIO
dashboard, ~175 Python modules, 40+ test files) but its dependencies are not
installable/runnable out of the box in a clean environment and the automated
test suite is not known-green. The goal is a repo where a newcomer can install,
run the test suite, and import both runtime entry points without errors — i.e.
"everything works properly" in the concrete, checkable sense the project's own
CI already defines (`pip install -r requirements.txt` then `pytest tests/`).

This is a **stabilization** effort, not a feature effort: fix what is broken so
the project is green and trustworthy, without changing trading behavior, adding
features, or doing large refactors.

## Requirements
- **R1 (Must):** `pip install -r requirements.txt` completes successfully on a
  clean Python 3.11/3.12 environment (the versions used locally and in CI).
- **R2 (Must):** `python -m pytest tests/` — the exact command CI runs —
  completes with **0 failures and 0 errors** and **no collection errors**. The
  whole suite is collected and run; no test files are excluded to hide failures.
- **R3 (Must):** Both runtime entry points import without error:
  `python -c "import async_scalp_engine"` and `python -c "import app"`.
- **R4 (Must):** Fixes preserve existing behavior and follow repo conventions in
  `CLAUDE.md` — DB writes only via `database.update_system_state()`, connections
  via `get_conn()`/`open_db()`, schema changes as idempotent migrations under
  `scripts/` mirrored in `init_db()`, **no file deletion** (move to `archive/`),
  Turkish `# NEDEN:` rationale on critical changes, fixed money/`%`/`R` formats.
- **R5 (Must):** No test is deleted, emptied, or blanket-skipped to reach green.
  A `skip`/`xfail` is allowed **only** when a test genuinely cannot run in a
  hermetic CI environment (no network / no live exchange / no real Redis), and
  each such marker carries a `# NEDEN:` justification.
- **R6 (Should):** No new version-specific breakage — the suite passes on the
  local interpreter (3.11) and stays compatible with CI's 3.12.

## Constraints
- **Tech stack:** Python; dependencies pinned in `requirements.txt`; `pytest`
  (+ `pytest-asyncio`). Shared state is SQLite + Redis; `conftest.py` disables
  Redis, the dashboard PIN, IP allow-listing, and forces `paper` mode for tests.
- **Conventions:** strictly follow `CLAUDE.md` (single-writer DB, connection
  discipline, idempotent migrations as the single DDL source, archive-don't-
  delete, `# NEDEN:` comments, fixed numeric formatting).
- **Environment:** local interpreter is Python 3.11.15; CI targets 3.12.
  Outbound network is governed by the environment's policy — tests must not
  depend on live network/exchange/Redis.
- **Non-goals (explicitly out of scope):** no new trading features, strategies,
  or indicators; no enabling live trading; no large refactors or framework
  swaps; no new lint/format/type-check/coverage gate added to CI; no UI
  redesign; `archive/` and `backtest_data/` are left as-is.

## Edge cases to handle
- **E1:** A test fails because of a real bug in source → fix the **source** with
  the minimal change, preserving intended behavior (don't weaken the test).
- **E2:** A test fails because of a stale/incorrect assertion or a bug in the
  test itself → fix the **test** to match correct intended behavior, with a
  `# NEDEN:` note explaining the correction.
- **E3:** A test needs an external service or network (Redis, exchange API,
  HTTP) → make it hermetic via mocking/fixtures consistent with `conftest.py`,
  or mark `skip`/`xfail` with justification (R5) — never require live services.
- **E4:** A test is flaky due to ordering or shared-state contamination → make
  it deterministic (proper fixtures/teardown, temp DB), don't delete it.
- **E5:** A dependency fails to install or import on the target Python (e.g. a
  yanked/incompatible pin, or a PEP-668 "externally managed" environment) →
  resolve via a minimal, justified `requirements.txt` adjustment and/or the
  documented supported install path; `requirements.txt` stays the source truth.
- **E6:** A 3.11-vs-3.12 language/stdlib difference surfaces → fix compatibly so
  both interpreters pass; don't break either.

## Definition of done
Each item is independently checkable by re-running the command:
- [ ] `pip install -r requirements.txt` exits 0 on a clean environment.
- [ ] `python -m pytest tests/` reports **0 failed, 0 errors**, with the full
      suite collected (no collection/import errors).
- [ ] `python -c "import async_scalp_engine"` exits 0.
- [ ] `python -c "import app"` exits 0.
- [ ] No test file was deleted or emptied; every added `skip`/`xfail` has a
      `# NEDEN:` justification and is genuinely environment-bound.
- [ ] The diff respects `CLAUDE.md` conventions (spot-checkable: no direct
      `system_state` SQL writes, migrations idempotent, no deletions).
- [ ] A closing summary lists what was broken and what was fixed, mapped to the
      criteria above.

## Open questions
- "Professional" is interpreted here as **green install + green test suite +
  importable entry points, with behavior unchanged** — the bar CI already
  encodes. Broader professionalization (a lint/format/type gate, packaging as an
  installable distribution, coverage thresholds, docs overhaul) is treated as
  out of scope unless you say otherwise.
- The done-criteria verify entry points **import**; they do not start the full
  engine/dashboard runtime, which needs Redis + exchange credentials. Say so if
  you want a live boot smoke-test added.
