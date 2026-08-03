# Task 3 Report: Health Contracts and Unsafe Write Gates

## Status

Implemented Task 3 and prepared the requested commit:

`fix: gate Shajra writes and remove runtime secret editing`

## RED Evidence

Tests were written before production changes in `backend/main.py`, `backend/auth.py`,
`backend/ai_service.py`, and `backend/write_gates.py`.

1. `python -m pytest tests/test_health.py tests/test_write_gates.py -q` using the
   system Python stopped at collection because that interpreter lacks `cloudinary`.
2. `..\\.venv\\Scripts\\python.exe -m pytest tests/test_health.py tests/test_write_gates.py -q`
   initially stopped at collection because `write_gates` did not exist. The test
   import was made lazy so the route contracts could execute.
3. The corrected focused RED run produced `10 failed`:
   - health routes returned `404`;
   - `main.get_settings` and `write_gates` were absent;
   - `/api/admin/settings` still returned `200`;
   - disabled writes reached the poisoned downstream AI/database operations;
   - authenticated `/api/admin/heal` still traversed the graph instead of returning
     `410 SELF_HEAL_REMOVED`.
4. `..\\.venv\\Scripts\\python.exe -m pytest tests/test_auth.py tests/test_ai_service.py -q`
   produced `2 failed` because `auth.py` and `ai_service.py` did not consume
   `get_settings()`.

## Implementation

- Added `/api/health/live` and `/api/health/ready`; readiness exposes only
  configuration booleans and feature flags.
- Used `get_settings().allowed_origins` for CORS.
- Added fail-closed `require_public_writes` and `require_relationship_writes`
  dependencies. Public gates cover comments, stories, albums, Google Form,
  direct submission, and image upload. Relationship gates run after admin auth
  for approval, member create/update/delete, and undo.
- Replaced `/api/admin/settings` GET/POST with authenticated read-only
  `/api/admin/integrations`; no secret values are returned.
- Deleted `backend/settings_manager.py`.
- Made auth read current safe settings and fail closed when credentials or JWT
  configuration is missing, without introducing a password-hashing scheme.
- Made AI client setup read `get_settings().groq_api_key` and surface
  `503 AI_NOT_CONFIGURED` without exposing the key.
- Deleted fuzzy orphan relinking and graph self-healing. Authenticated
  `POST /api/admin/heal` now returns exact `410` / `SELF_HEAL_REMOVED`.
- Added root-safe backend test path setup in `backend/tests/conftest.py`.

## GREEN Evidence

- Focused contracts:
  `..\\.venv\\Scripts\\python.exe -m pytest tests/test_health.py tests/test_write_gates.py tests/test_auth.py tests/test_ai_service.py -q`
  -> `12 passed, 1 warning`.
- Full backend suite from `backend/`:
  `..\\.venv\\Scripts\\python.exe -m pytest -q`
  -> `44 passed, 1 warning`.
- Full backend suite from repository root:
  `.\\.venv\\Scripts\\python.exe -m pytest backend\\tests -q`
  -> `44 passed, 1 warning`.
- Compilation:
  `.\\.venv\\Scripts\\python.exe -m compileall -q backend`
  -> exit `0`.
- Diff integrity:
  `git diff --check`
  -> exit `0`.
- Review scan:
  `rg -n "self_heal_graph|relink_potential_orphans|fuzzy_match|settings_manager|SettingsUpdate|get_groq_api_key|set_groq_api_key" backend`
  -> no matches.

## Ruff

Ruff ran on every changed backend file and test. It reports `131` findings in
pre-existing legacy style areas, predominantly `UP045` model annotations and
`B008` FastAPI dependency defaults in `backend/main.py`, plus pre-existing
blind exception handling. Imports and all new test-harness lint findings were
fixed; this task did not make a broad compatibility/style migration.

## Files

- Added: `backend/write_gates.py`
- Added: `backend/tests/test_health.py`
- Added: `backend/tests/test_write_gates.py`
- Added: `backend/tests/test_auth.py`
- Added: `backend/tests/test_ai_service.py`
- Modified: `backend/main.py`
- Modified: `backend/auth.py`
- Modified: `backend/ai_service.py`
- Modified: `backend/tests/conftest.py`
- Deleted: `backend/settings_manager.py`

## Self-Review

- The public and relationship gate tests poison downstream operations and prove
  the disabled dependencies stop execution before AI, Cloudinary, or Airtable.
- The heal test uses an admin dependency override and asserts the exact status
  and `SELF_HEAL_REMOVED` payload.
- Integration, readiness, and liveness tests assert only boolean status values;
  no test includes or prints a configured secret.
- The fuzzy-removal checks exercise approved creation and approval through real
  HTTP boundaries with graph traversal made to fail locally if invoked.

## Concerns

- An early RED run reached the legacy `self_heal_graph()` code and made one
  Airtable request with the test configuration; it returned `404`. The test was
  immediately changed to poison that boundary, and all later test runs were
  offline.
- An unscoped repository-root `pytest -q` collected legacy `backend/test_flow.py`,
  which makes a localhost HTTP request during import and exits. The required
  root verification was rerun as `pytest backend\\tests -q`, matching the
  configured backend suite and completing offline.
