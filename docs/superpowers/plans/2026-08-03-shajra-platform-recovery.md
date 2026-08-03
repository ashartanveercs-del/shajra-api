# Shajra Platform Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a secure, tested, Railway-free local baseline that is ready for isolated Vercel previews without changing cloud or production state.

**Architecture:** Keep the current v1 routes temporarily, but place configuration, write gates, HTTP errors, and test tooling around them. Production configuration fails closed, every unsafe write is disabled by default, and the frontend has one typed HTTP boundary with explicit loading and failure states.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings 2.14.2, pytest 9.1.1, Next.js 16.2.12, React 19.2.8, TypeScript, Vitest 4.1.10, Testing Library.

## Global Constraints

- Work in an isolated Git worktree created with `superpowers:using-git-worktrees`.
- Do not push, deploy, change Vercel, touch Airtable, create Cloudinary assets, or create Upstash resources in this plan.
- Introduce no paid service and no Railway dependency or fallback.
- `PUBLIC_WRITES_ENABLED`, `RELATIONSHIP_WRITES_ENABLED`, and `NORMALIZED_READS_ENABLED` default to `false`.
- Never print, persist, or return secret values.
- Preserve v1 read behavior until the normalized backend plan replaces it.
- Use ASCII for new source files and keep the repository free of backups, migration artifacts, `.vercel/`, and local settings.
- Every task ends with its focused tests passing and a local commit.

---

## File Structure

Create:

- `backend/.python-version`: Python runtime pin.
- `.env.example`: backend environment variable names with non-secret examples.
- `backend/requirements-dev.txt`: local test and static-analysis tools.
- `backend/pytest.ini`: pytest discovery and import path.
- `backend/tests/conftest.py`: safe test environment and fixtures.
- `backend/tests/test_config.py`: fail-closed settings tests.
- `backend/tests/test_health.py`: liveness and readiness contract tests.
- `backend/tests/test_write_gates.py`: disabled-write contract tests.
- `backend/write_gates.py`: FastAPI dependencies for feature flags.
- `frontend/.env.example`: frontend environment names.
- `frontend/vitest.config.ts`: browser-unit-test configuration.
- `frontend/src/test/setup.ts`: DOM matchers and cleanup.
- `frontend/src/lib/env.ts`: API URL resolution.
- `frontend/src/lib/http.ts`: typed fetch wrapper and `ApiProblem`.
- `frontend/src/components/feedback/AsyncState.tsx`: shared route states.
- `frontend/src/lib/env.test.ts`, `frontend/src/lib/http.test.ts`: configuration and HTTP tests.
- `.github/workflows/ci.yml`: local-equivalent validation in GitHub.

Modify:

- `.gitignore`
- `backend/requirements.txt`
- `backend/config.py`
- `backend/auth.py`
- `backend/main.py`
- `backend/ai_service.py`
- `backend/api/index.py`
- `frontend/package.json`, `frontend/package-lock.json`
- `frontend/src/lib/api.ts`
- Public route files under `frontend/src/app/`
- `frontend/src/components/GlobeMap.tsx`
- `frontend/eslint.config.mjs`, `frontend/tsconfig.json`

Delete after callers are migrated:

- `backend/settings_manager.py`

## Interfaces

```python
class Settings(BaseSettings):
    app_env: Literal["development", "test", "preview", "production"]
    public_writes_enabled: bool
    relationship_writes_enabled: bool
    normalized_reads_enabled: bool

def get_settings() -> Settings: ...
def require_public_writes() -> None: ...
def require_relationship_writes() -> None: ...
```

```ts
export class ApiProblem extends Error {
  status: number;
  code: string;
  requestId?: string;
}

export function resolveApiBase(env?: Record<string, string | undefined>): string;
export function requestJson<T>(path: string, init?: RequestInit): Promise<T>;
```

### Task 1: Pin Runtimes and Add Test Harnesses

**Files:**
- Create: `backend/.python-version`
- Create: `backend/requirements-dev.txt`
- Create: `backend/pytest.ini`
- Create: `backend/tests/conftest.py`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/src/test/setup.ts`
- Modify: `backend/requirements.txt`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

**Interfaces:**
- Produces: `pytest` and `npm test` commands used by every later task.

- [ ] **Step 1: Pin the backend and developer tools**

Write `backend/.python-version`:

```text
3.12
```

Add to `backend/requirements.txt`:

```text
pydantic-settings==2.14.2
```

Write `backend/requirements-dev.txt`:

```text
-r requirements.txt
httpx==0.28.1
mypy==2.3.0
pip-audit==2.10.1
pytest==9.1.1
pytest-cov==7.1.0
ruff==0.16.1
```

- [ ] **Step 2: Configure pytest**

Write `backend/pytest.ini`:

```ini
[pytest]
addopts = -ra --strict-markers
python_files = test_*.py
testpaths = tests
```

At the top of `backend/tests/conftest.py`, set only test values before importing the app:

```python
import os

os.environ.update(
    {
        "APP_ENV": "test",
        "AIRTABLE_PAT": "test-token",
        "AIRTABLE_BASE_ID": "app-test",
        "ADMIN_USERNAME": "admin-test",
        "ADMIN_PASSWORD_HASH": "test-only-hash",
        "JWT_SECRET": "test-secret-at-least-32-characters-long",
        "JWT_ISSUER": "shajra-test",
        "JWT_AUDIENCE": "shajra-admin-test",
        "PUBLIC_WRITES_ENABLED": "false",
        "RELATIONSHIP_WRITES_ENABLED": "false",
        "NORMALIZED_READS_ENABLED": "false",
    }
)
```

- [ ] **Step 3: Upgrade the patched frontend runtime and add unit-test tools**

Run:

```powershell
cd D:\andrew\shajra-api\frontend
npm install --save-exact next@16.2.12 react@19.2.8 react-dom@19.2.8
npm install --save-dev --save-exact eslint-config-next@16.2.12 vitest@4.1.10 jsdom@30.0.1 @testing-library/react@16.3.2 @testing-library/jest-dom@7.0.0 @testing-library/user-event@14.6.1
```

Add scripts to `frontend/package.json`:

```json
{
  "test": "vitest run",
  "test:watch": "vitest",
  "typecheck": "tsc --noEmit"
}
```

- [ ] **Step 4: Configure Vitest**

Write `frontend/vitest.config.ts`:

```ts
import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    passWithNoTests: true,
    restoreMocks: true,
    clearMocks: true,
  },
});
```

Write `frontend/src/test/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(cleanup);
```

- [ ] **Step 5: Verify both harnesses**

Run:

```powershell
cd D:\andrew\shajra-api\backend
python -m pip install -r requirements-dev.txt
python -m pytest

cd D:\andrew\shajra-api\frontend
npm test
npm run typecheck
```

Expected: both test runners exit `0`; Vitest may report no test files only until Task 4.

- [ ] **Step 6: Commit the harness**

```powershell
git add backend/.python-version backend/requirements.txt backend/requirements-dev.txt backend/pytest.ini backend/tests/conftest.py frontend/package.json frontend/package-lock.json frontend/vitest.config.ts frontend/src/test/setup.ts
git commit -m "test: add Shajra backend and frontend harnesses"
```

### Task 2: Add Fail-Closed Settings

**Files:**
- Create: `backend/tests/test_config.py`
- Create: `.env.example`
- Create: `frontend/.env.example`
- Modify: `backend/config.py`

**Interfaces:**
- Produces: `Settings`, `get_settings()`, and compatibility constants for current v1 modules.

- [ ] **Step 1: Write failing production-settings tests**

Create `backend/tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from config import Settings


def test_production_rejects_missing_secrets():
    with pytest.raises(ValidationError):
        Settings(app_env="production", _env_file=None)


def test_test_environment_defaults_all_writes_off():
    settings = Settings(
        app_env="test",
        airtable_pat="test-token",
        airtable_base_id="app-test",
        admin_username="admin",
        admin_password_hash="hash",
        jwt_secret="x" * 32,
        jwt_issuer="shajra-test",
        jwt_audience="shajra-admin-test",
        _env_file=None,
    )
    assert settings.public_writes_enabled is False
    assert settings.relationship_writes_enabled is False
    assert settings.normalized_reads_enabled is False
```

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `python -m pytest tests/test_config.py -q`

Expected: FAIL because `Settings` does not exist.

- [ ] **Step 3: Replace default secrets with typed settings**

Implement this shape in `backend/config.py`:

```python
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "preview", "production"] = "development"
    airtable_pat: SecretStr | None = None
    airtable_base_id: str | None = None
    groq_api_key: SecretStr | None = None
    cloudinary_url: SecretStr | None = None
    admin_username: str | None = None
    admin_password_hash: SecretStr | None = None
    jwt_secret: SecretStr | None = None
    mutation_preview_secret: SecretStr | None = None
    jwt_issuer: str = "shajra"
    jwt_audience: str = "shajra-admin"
    cors_allowed_origins: str = "http://localhost:3000"
    public_writes_enabled: bool = False
    relationship_writes_enabled: bool = False
    normalized_reads_enabled: bool = False

    @model_validator(mode="after")
    def require_runtime_secrets(self) -> "Settings":
        if self.app_env in {"preview", "production"}:
            required = {
                "AIRTABLE_PAT": self.airtable_pat,
                "AIRTABLE_BASE_ID": self.airtable_base_id,
                "ADMIN_USERNAME": self.admin_username,
                "ADMIN_PASSWORD_HASH": self.admin_password_hash,
                "JWT_SECRET": self.jwt_secret,
                "MUTATION_PREVIEW_SECRET": self.mutation_preview_secret,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                raise ValueError("Missing required settings: " + ", ".join(sorted(missing)))
        return self

    @property
    def allowed_origins(self) -> list[str]:
        return [value.strip() for value in self.cors_allowed_origins.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

Keep temporary compatibility constants derived from `get_settings()` only where
the current v1 modules still import them. Never unwrap a `SecretStr` for logging
or API serialization.

- [ ] **Step 4: Add non-secret environment templates**

Write root `.env.example` with these exact keys and safe flags. Local development
copies it to root `.env`, matching `ENV_FILE`; Vercel continues to inject dashboard
environment variables and does not require a file:

```dotenv
APP_ENV=development
AIRTABLE_PAT=
AIRTABLE_BASE_ID=
GROQ_API_KEY=
CLOUDINARY_URL=
ADMIN_USERNAME=
ADMIN_PASSWORD_HASH=
JWT_SECRET=
MUTATION_PREVIEW_SECRET=
JWT_ISSUER=shajra
JWT_AUDIENCE=shajra-admin
CORS_ALLOWED_ORIGINS=http://localhost:3000
PUBLIC_WRITES_ENABLED=false
RELATIONSHIP_WRITES_ENABLED=false
NORMALIZED_READS_ENABLED=false
```

Write `frontend/.env.example`:

```dotenv
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
BACKEND_API_URL=http://127.0.0.1:8000
```

- [ ] **Step 5: Run tests and static checks**

Run:

```powershell
python -m pytest tests/test_config.py -q
ruff check config.py tests/test_config.py
```

Expected: PASS.

- [ ] **Step 6: Commit settings**

```powershell
git add backend/config.py .env.example frontend/.env.example backend/tests/test_config.py
git commit -m "fix: fail closed when Shajra settings are missing"
```

### Task 3: Add Health Contracts and Disable Unsafe Writes

**Files:**
- Create: `backend/write_gates.py`
- Create: `backend/tests/test_health.py`
- Create: `backend/tests/test_write_gates.py`
- Modify: `backend/main.py`
- Modify: `backend/auth.py`
- Modify: `backend/ai_service.py`
- Delete: `backend/settings_manager.py`

**Interfaces:**
- Consumes: `get_settings()` from Task 2.
- Produces: `/api/health/live`, `/api/health/ready`, and fail-closed write dependencies.

- [ ] **Step 1: Write failing API contract tests**

Create `backend/tests/test_health.py`:

```python
from fastapi.testclient import TestClient
from main import app


client = TestClient(app)


def test_liveness_has_no_secret_values():
    response = client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_flags_without_values():
    response = client.get("/api/health/ready")
    assert response.status_code == 200
    assert response.json()["writes"] == {"public": False, "relationships": False}
```

Create `backend/tests/test_write_gates.py` with these exact contracts: public
submission returns `503 PUBLIC_WRITES_DISABLED`; the relationship dependency
returns `503 RELATIONSHIP_WRITES_DISABLED` when called with test settings; and
the removed self-heal endpoint returns `410 GRAPH_HEAL_REMOVED` after the test
admin dependency is overridden with a valid identity. The public route assertion
starts as follows:

```python
from fastapi.testclient import TestClient
from main import app


client = TestClient(app)


def test_public_submission_is_disabled_by_default():
    response = client.post("/api/submit", json={"fullName": "Test Person"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "PUBLIC_WRITES_DISABLED"


def test_graph_heal_is_permanently_gone():
    response = client.post("/api/admin/heal")
    assert response.status_code in {401, 410}
```

- [ ] **Step 2: Run focused tests and confirm failure**

Run: `python -m pytest tests/test_health.py tests/test_write_gates.py -q`

Expected: FAIL because health routes and gates are absent.

- [ ] **Step 3: Implement feature-gate dependencies**

Write `backend/write_gates.py`:

```python
from fastapi import HTTPException

from config import get_settings


def require_public_writes() -> None:
    if not get_settings().public_writes_enabled:
        raise HTTPException(
            status_code=503,
            detail={"code": "PUBLIC_WRITES_DISABLED", "message": "Submissions are temporarily unavailable."},
        )


def require_relationship_writes() -> None:
    if not get_settings().relationship_writes_enabled:
        raise HTTPException(
            status_code=503,
            detail={"code": "RELATIONSHIP_WRITES_DISABLED", "message": "Relationship editing is temporarily unavailable."},
        )
```

- [ ] **Step 4: Wire health, CORS, and gates in `backend/main.py`**

Use `get_settings().allowed_origins` instead of `allow_origins=["*"]`. Add:

```python
@app.get("/api/health/live")
def health_live():
    return {"status": "ok"}


@app.get("/api/health/ready")
def health_ready():
    settings = get_settings()
    return {
        "status": "ready",
        "environment": settings.app_env,
        "configured": {
            "airtable": bool(settings.airtable_pat and settings.airtable_base_id),
            "groq": bool(settings.groq_api_key),
            "cloudinary": bool(settings.cloudinary_url),
        },
        "writes": {
            "public": settings.public_writes_enabled,
            "relationships": settings.relationship_writes_enabled,
        },
        "normalizedReads": settings.normalized_reads_enabled,
    }
```

Add `Depends(require_public_writes)` to `POST /api/comments`, `/api/stories`,
`/api/albums`, `/api/webhook/google-form`, `/api/submit`, and `/api/upload-image`.
Add `Depends(require_relationship_writes)` after authentication to approval,
member create/update/delete, undo, and relationship-changing admin routes. Make
`POST /api/admin/heal` return `410 Gone` with code `SELF_HEAL_REMOVED`.

- [ ] **Step 5: Remove runtime secret editing**

Delete `backend/settings_manager.py`. Update `backend/ai_service.py` to consume
`get_settings().groq_api_key` and fail with `AI_NOT_CONFIGURED` without returning
the key. Replace `/api/admin/settings` GET/POST with read-only integration status:

```python
@app.get("/api/admin/integrations")
def admin_integrations(admin=Depends(get_current_admin)):
    settings = get_settings()
    return {
        "groqConfigured": bool(settings.groq_api_key),
        "cloudinaryConfigured": bool(settings.cloudinary_url),
        "coordinationConfigured": False,
    }
```

- [ ] **Step 6: Run the backend gate**

Run:

```powershell
python -m pytest tests/test_health.py tests/test_write_gates.py -q
ruff check .
python -m compileall -q .
```

Expected: PASS with no network requests.

- [ ] **Step 7: Commit runtime hardening**

```powershell
git add backend/main.py backend/auth.py backend/ai_service.py backend/write_gates.py backend/tests/test_health.py backend/tests/test_write_gates.py
git rm backend/settings_manager.py
git commit -m "fix: gate Shajra writes and remove runtime secret editing"
```

### Task 4: Replace the Railway Fallback with a Typed HTTP Boundary

**Files:**
- Create: `frontend/src/lib/env.ts`
- Create: `frontend/src/lib/env.test.ts`
- Create: `frontend/src/lib/http.ts`
- Create: `frontend/src/lib/http.test.ts`
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Consumes: `NEXT_PUBLIC_API_URL`.
- Produces: `resolveApiBase`, `ApiProblem`, and `requestJson<T>`.

- [ ] **Step 1: Write failing environment tests**

Create `frontend/src/lib/env.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { resolveApiBase } from "./env";

describe("resolveApiBase", () => {
  it("strips trailing slashes", () => {
    expect(resolveApiBase({ NODE_ENV: "production", NEXT_PUBLIC_API_URL: "https://api.example.com///" }))
      .toBe("https://api.example.com");
  });

  it("fails production without a configured API", () => {
    expect(() => resolveApiBase({ NODE_ENV: "production" })).toThrow("NEXT_PUBLIC_API_URL");
  });

  it("uses localhost only in development and test", () => {
    expect(resolveApiBase({ NODE_ENV: "test" })).toBe("http://127.0.0.1:8000");
  });
});
```

- [ ] **Step 2: Run the test and confirm failure**

Run: `npm test -- src/lib/env.test.ts`

Expected: FAIL because `env.ts` does not exist.

- [ ] **Step 3: Implement API URL resolution**

Write `frontend/src/lib/env.ts`:

```ts
export function resolveApiBase(
  env: Record<string, string | undefined> = process.env,
): string {
  const configured = env.NEXT_PUBLIC_API_URL?.trim();
  if (configured) return configured.replace(/\/+$/, "");
  if (env.NODE_ENV === "development" || env.NODE_ENV === "test") {
    return "http://127.0.0.1:8000";
  }
  throw new Error("NEXT_PUBLIC_API_URL is required outside development and test");
}

export const API_BASE = resolveApiBase();
```

- [ ] **Step 4: Write failing HTTP wrapper tests**

Create `frontend/src/lib/http.test.ts` to mock `fetch`, assert JSON success, and
assert a `503` body becomes `ApiProblem` with code `PUBLIC_WRITES_DISABLED`.
Use this exact assertion:

```ts
await expect(requestJson("/api/submit", { method: "POST" })).rejects.toMatchObject({
  status: 503,
  code: "PUBLIC_WRITES_DISABLED",
});
```

- [ ] **Step 5: Implement `requestJson`**

Write `frontend/src/lib/http.ts`:

```ts
import { API_BASE } from "./env";

export class ApiProblem extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
  ) {
    super(message);
    this.name = "ApiProblem";
  }
}

export async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    throw new ApiProblem(
      response.status,
      detail?.code ?? body?.code ?? "REQUEST_FAILED",
      detail?.message ?? body?.message ?? `Request failed with ${response.status}`,
      response.headers.get("x-request-id") ?? undefined,
    );
  }
  return body as T;
}
```

- [ ] **Step 6: Route all public functions through `requestJson`**

Remove the Railway constant and direct `fetch` calls from `frontend/src/lib/api.ts`.
Preserve every current exported function name for this plan. Convert each exported
HTTP function to `requestJson`; this is the required shape for `fetchTree`:

```ts
export function fetchTree(): Promise<Member[]> {
  return requestJson<Member[]>("/api/tree", { cache: "no-store" });
}
```

Convert members, map, search, comments, stories, albums, submissions, image upload,
and admin reads. Keep bearer-token admin signatures only until the frontend plan
introduces the same-origin proxy. Add a source assertion that `fetch(` appears only
in `frontend/src/lib/http.ts` and Next.js route handlers.

- [ ] **Step 7: Verify there is no Railway reference**

Run:

```powershell
npm test -- src/lib/env.test.ts src/lib/http.test.ts
rg -n "railway\.app|shajra-api-production" frontend backend
```

Expected: tests PASS and `rg` returns no matches.

- [ ] **Step 8: Commit the HTTP boundary**

```powershell
git add frontend/src/lib/env.ts frontend/src/lib/env.test.ts frontend/src/lib/http.ts frontend/src/lib/http.test.ts frontend/src/lib/api.ts
git commit -m "fix: remove Railway fallback from Shajra frontend"
```

### Task 5: Make Public Failures Visible and Clear the Lint Baseline

**Files:**
- Create: `frontend/src/components/feedback/AsyncState.tsx`
- Modify: `frontend/src/app/page.tsx`
- Modify: `frontend/src/app/tree/page.tsx`
- Modify: `frontend/src/app/search/page.tsx`
- Modify: `frontend/src/app/map/page.tsx`
- Modify: `frontend/src/app/member/[id]/page.tsx`
- Modify: `frontend/src/app/submit/page.tsx`
- Modify: `frontend/src/app/admin/page.tsx`
- Modify: `frontend/src/components/AdminTreeEditor.tsx`
- Modify: `frontend/src/components/GlobeMap.tsx`

**Interfaces:**
- Consumes: `ApiProblem` from Task 4.
- Produces: explicit loading, empty, error, partial, and ready UI states.

- [ ] **Step 1: Write a failing shared-state component test**

Add a test beside the component that renders the error state and asserts the retry
button calls its handler:

```tsx
render(<AsyncState state="error" title="Tree unavailable" actionLabel="Retry" onAction={retry} />);
await user.click(screen.getByRole("button", { name: "Retry" }));
expect(retry).toHaveBeenCalledOnce();
```

- [ ] **Step 2: Implement the state component**

Use this contract:

```ts
type AsyncStateProps = {
  state: "loading" | "empty" | "error" | "partial";
  title: string;
  message?: string;
  actionLabel?: string;
  onAction?: () => void;
};
```

Render loading with `role="status"`, errors with `role="alert"`, and a real button
for retry. Do not render feature descriptions or instructions inside the app.

- [ ] **Step 3: Replace silent catches route by route**

On home, tree, search, map, member detail, submit, and admin, store a discriminated
state instead of returning `[]` after network errors. Apply the same state to data
loads owned by `AdminTreeEditor` and `GlobeMap`:

```ts
type Loadable<T> =
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "empty" }
  | { status: "error"; problem: ApiProblem };
```

On the tree page, remove the condition that renders nothing when `error` is set.
Show `Tree unavailable`, the server message, and Retry. Submission errors remain
inline and preserve every entered value.

- [ ] **Step 4: Remove all explicit `any` and effect-state lint errors**

Replace caught values with `unknown` and narrow them:

```ts
function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : "Unexpected error";
}
```

Initialize local-storage-derived state lazily only as a temporary compatibility
measure:

```ts
const [token, setToken] = useState<string | null>(() =>
  typeof window === "undefined" ? null : localStorage.getItem("shajra_admin_token"),
);
```

The frontend plan removes local storage entirely. Replace unused imports, type
GlobeMap values, and replace unsafe `<img>` uses with `next/image` where dimensions
are known.

- [ ] **Step 5: Run the complete frontend gate**

Run:

```powershell
npm run lint
npm run typecheck
npm test
npm run build
```

Expected: all commands exit `0`; ESLint reports zero errors.

- [ ] **Step 6: Commit visible states and lint cleanup**

```powershell
git add frontend/src
git commit -m "fix: show Shajra route failures and clear lint"
```

### Task 6: Add Repeatable Local and GitHub Gates

**Files:**
- Modify: `.gitignore`
- Create: `.github/workflows/ci.yml`
- Modify: `frontend/README.md`

**Interfaces:**
- Consumes: all test commands from Tasks 1-5.
- Produces: a pull-request gate without deployment permissions.

- [ ] **Step 1: Protect local and migration artifacts**

Add to `.gitignore`:

```gitignore
.vercel/
.env
*.age
settings.json
backups/
migration-artifacts/
frontend/playwright-report/
frontend/test-results/
backend/.coverage
backend/htmlcov/
```

- [ ] **Step 2: Add CI with no cloud secrets**

Create `.github/workflows/ci.yml` with two jobs. The backend job installs
`backend/requirements-dev.txt` and runs:

```powershell
python -m pytest backend/tests -q
ruff check backend
python -m compileall -q backend
pip-audit -r backend/requirements.txt
```

The frontend job uses Node 22, runs `npm ci` in `frontend/`, then:

```powershell
npm audit --audit-level=high
npm run lint
npm run typecheck
npm test
npm run build
```

Do not add deployment steps, repository secrets, or production environment values.

- [ ] **Step 3: Document exact local verification**

Replace the boilerplate `frontend/README.md` with repository-specific commands,
the two local URLs, and the statement that all write flags remain `false` during
this plan. Do not include credentials.

- [ ] **Step 4: Run the same gate locally**

Run all commands from Step 2. Also run:

```powershell
git diff --check
rg -n "railway\.app|shajra-api-production|shajrasecure123|shajra-jwt-secret" . -g '!docs/superpowers/specs/**' -g '!docs/superpowers/plans/**'
```

Expected: all gates pass and the secret/fallback scan has no source matches.

- [ ] **Step 5: Commit the recovery baseline**

```powershell
git add .gitignore .github/workflows/ci.yml frontend/README.md
git commit -m "ci: gate the Shajra recovery baseline"
```

## Completion Gate

This plan is complete only when the worktree is clean, every command in Task 6
passes, no cloud state changed, no Railway URL remains in source, and all public
or relationship writes still fail closed by default. Continue with
`2026-08-03-shajra-graph-core.md`.
