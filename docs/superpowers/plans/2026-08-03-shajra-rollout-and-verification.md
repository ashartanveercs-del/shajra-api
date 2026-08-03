# Shajra Rollout and Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the tested Shajra frontend, backend, and normalized graph to free Vercel hosting with isolated previews, reviewed data migration, observable promotion, and a proven rollback path.

**Architecture:** Git-linked Vercel projects keep separate `frontend/` and `backend/` roots and use Related Projects for matched previews. Staging Airtable, namespaced Cloudinary, and namespaced Upstash isolate tests. Production is promoted backend first, then frontend, with writes and AI enrichment disabled; data migration, write enablement, and AI enablement are later, separately reviewed gates.

**Tech Stack:** Vercel Hobby, GitHub, Airtable, Cloudinary, Upstash Redis Free, PowerShell 7, GitHub CLI, Vercel CLI, Playwright, the Shajra operator CLI.

## Global Constraints

- Complete every prior plan with clean worktrees and passing gates.
- Never print or commit the Vercel token, Airtable PAT, Cloudinary URL, JWT secret, Groq key, webhook secret, admin hash, or Upstash token.
- Use only free tiers. Stop before any screen or API operation that enables billing.
- Preview credentials never point to the production Airtable base or production Cloudinary prefix.
- Related Projects require Git deployments; do not use CLI-created previews for cross-project testing.
- Promote backend before frontend.
- Production starts with `PUBLIC_WRITES_ENABLED=false`, `RELATIONSHIP_WRITES_ENABLED=false`, `NORMALIZED_READS_ENABLED=false`, and `AI_ENRICHMENT_ENABLED=false`.
- Do not use a successful production form submission or relationship mutation as a smoke test without explicit approval at that moment.
- Migration requires a reviewed dry-run, encrypted backup, successful staging restore, exact plan SHA, and explicit apply approval.
- Rollback never deletes normalized records.
- Do not delete the Railway project; removing Railway configuration from the repository is sufficient.
- Rotate the Vercel credential shared in chat after configuration and verification.

---

## Known Project Coordinates

```text
GitHub repository: ashartanveercs-del/shajra-api
Vercel team slug: ashartanveercs-dels-projects
Backend project: backend
Backend project ID: prj_dAjxmpk6YSA82O82YVKoeDO28TxX
Frontend project: frontend
Frontend project ID: prj_czIE5tUdSispd2zuDUEu0mOuF1zO
Backend production domain: https://backend-one-xi-26.vercel.app
Frontend production domain: https://shajraheritage.vercel.app
```

## File Structure

Create:

- `frontend/vercel.json`: Related Projects declaration.
- `frontend/src/lib/server/related-backend.ts`: matched backend host resolution.
- `scripts/vercel/common.ps1`: authenticated REST helpers with value redaction.
- `scripts/vercel/audit-projects.ps1`: read-only roots, env names, and deployment state.
- `scripts/vercel/configure-projects.ps1`: dry-run-by-default root and env changes.
- `scripts/vercel/verify-deployments.ps1`: read-only endpoint and deployment checks.
- `docs/runbooks/shajra-deployment.md`
- `docs/runbooks/shajra-migration.md`
- `docs/runbooks/shajra-rollback.md`
- `.github/dependabot.yml`

Modify:

- `frontend/package.json`, `frontend/package-lock.json`
- `frontend/src/lib/server/backend.ts`
- `.github/workflows/ci.yml`
- `.gitignore`

Delete only after successful production verification:

- `railway.json`
- root `Procfile`, root `requirements.txt`
- `backend/Procfile`
- superseded ad hoc setup, seed, debug, and repair scripts listed in the migration runbook.

## External Mutation Gates

1. **Preview resources:** create staging Airtable, Upstash Free, and preview configuration.
2. **Project configuration:** change Vercel roots and environment variables.
3. **Preview Git state:** push the feature branch and create a PR.
4. **Production code:** promote backend and frontend with all writes disabled.
5. **Production data:** apply the reviewed migration.
6. **Production writes:** enable public writes, then relationship writes.
7. **AI enrichment:** enable the review-only pipeline after synthetic preview proof.
8. **Integration:** merge to `main`, tag, and rotate the Vercel token.

Execution must announce each gate before mutation and record the resulting resource
or deployment ID without recording secret values.

### Task 1: Add Monorepo Deployment Configuration and Dry-Run Scripts

**Files:**
- Create: `frontend/vercel.json`
- Create: `frontend/src/lib/server/related-backend.ts`
- Create: `scripts/vercel/common.ps1`
- Create: `scripts/vercel/audit-projects.ps1`
- Create: `scripts/vercel/configure-projects.ps1`
- Create: script tests or dry-run assertions.
- Modify: `frontend/package.json`, `frontend/package-lock.json`

**Interfaces:**
- Produces: matched preview backend resolution and auditable Vercel configuration.

- [ ] **Step 1: Install Related Projects support**

Run:

```powershell
cd D:\andrew\shajra-api\frontend
npm install --save-exact @vercel/related-projects@1.1.0
```

Write `frontend/vercel.json`:

```json
{
  "$schema": "https://openapi.vercel.sh/vercel.json",
  "relatedProjects": ["prj_dAjxmpk6YSA82O82YVKoeDO28TxX"]
}
```

- [ ] **Step 2: Resolve the related backend server-side**

In `related-backend.ts`:

```ts
import { withRelatedProject } from "@vercel/related-projects";

export function relatedBackendUrl(): string {
  const configured = process.env.BACKEND_API_URL?.trim();
  const host = process.env.VERCEL_ENV === "preview"
    ? withRelatedProject({ projectName: "backend", defaultHost: configured })
    : configured;
  if (!host) throw new Error("Matching backend deployment is unavailable");
  return host.startsWith("http") ? host.replace(/\/+$/, "") : `https://${host.replace(/\/+$/, "")}`;
}
```

The function invokes Related Projects only in a Git-created Vercel preview. In
production and local environments it requires the server-only `BACKEND_API_URL`.
Unit tests cover all three environments and a missing-host failure.

- [ ] **Step 3: Write redacting Vercel REST helpers**

`common.ps1` must stop when `$env:VERCEL_TOKEN` is empty, resolve the team ID from
the known team slug, attach bearer authorization, and never output request bodies
or response environment values. Export `Invoke-VercelGet`, `Invoke-VercelPatch`,
and `Get-VercelTeamId`.

Use this guard:

```powershell
if ([string]::IsNullOrWhiteSpace($env:VERCEL_TOKEN)) {
    throw 'VERCEL_TOKEN must be set in the current process.'
}
```

- [ ] **Step 4: Implement read-only project audit**

`audit-projects.ps1` fetches both known project IDs and prints only project name,
root directory, production branch, framework, environment variable names and
targets, latest deployment ID/status, and domains. It must not fetch environment
variable values.

- [ ] **Step 5: Implement dry-run-by-default configuration**

`configure-projects.ps1` accepts `-Apply`. Without it, print this exact intended
diff and exit `0`:

```text
backend.rootDirectory: null -> backend
frontend.rootDirectory: null -> frontend
frontend.relatedProjects: repository configuration
```

With `-Apply`, PATCH backend root to `backend` and frontend root to `frontend`.
Environment synchronization reads values from current process variables by an
explicit allowlist and sends them as sensitive variables; logs show only key,
target, and project. Never delete an existing key not in the allowlist.

- [ ] **Step 6: Test dry-run and build**

Run without a token and assert the audit fails before network. Run configure
without `-Apply` and assert no REST mutation helper is called. Then:

```powershell
cd D:\andrew\shajra-api\frontend
npm run typecheck
npm test -- src/lib/server
npm run build
```

- [ ] **Step 7: Commit deployment configuration**

```powershell
git add frontend/vercel.json frontend/package.json frontend/package-lock.json frontend/src/lib/server scripts/vercel
git commit -m "chore: prepare Shajra Vercel monorepo previews"
```

### Task 2: Write Deployment, Migration, and Rollback Runbooks

**Files:**
- Create: three runbooks listed in File Structure.
- Create: `.github/dependabot.yml`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: operator checklists with evidence locations and stop conditions.

- [ ] **Step 1: Write the deployment runbook**

Include prerequisites, known project IDs/domains, exact environment key names,
root settings, Related Projects behavior, branch-preview order, backend-first
promotion, verification commands, monitoring window, and token rotation. Every
mutation command is preceded by `MUTATION` and every read-only command by `READ`.
Include the AI enrichment flag, prompt/model version evidence, synthetic preview
test, first-production-use approval, and immediate disable procedure.

- [ ] **Step 2: Write the migration runbook**

Include backup passphrase handling, production preflight, encrypted backup,
staging restore, audit, plan SHA, ambiguity review, apply gate, exact count and
checksum verification, normalized-read flag, and the rule that legacy data is not
deleted.

- [ ] **Step 3: Write the rollback runbook**

Define code rollback, normalized-read rollback, interrupted operation recovery,
frontend-only rollback, backend-only rollback, and credential compromise. State
that once normalized writes are enabled, rollback must use a backend version that
understands normalized storage. AI rollback first disables enrichment, leaves raw
submissions/manual review available, and preserves append-only attempt events.

- [ ] **Step 4: Add dependency monitoring and CI integration tests**

Configure weekly Dependabot for npm, pip, and GitHub Actions. Extend CI to run
Playwright fixture tests and fake-provider AI tests but never Vercel, Airtable,
Cloudinary, Upstash, or Groq writes.

- [ ] **Step 5: Self-check and commit**

```powershell
rg -n "VERCEL_TOKEN=|AIRTABLE_PAT=|JWT_SECRET=|MUTATION_PREVIEW_SECRET=|CLOUDINARY_URL=|GROQ_API_KEY=|UPSTASH_REDIS_REST_TOKEN=" docs scripts .github
git diff --check
git add docs/runbooks .github
git commit -m "docs: add Shajra deployment and rollback runbooks"
```

Expected: secret scan has no assigned values.

### Task 3: Preview Resource Gate

**Files:**
- Write redacted external evidence only: `D:\shajra-rollout-evidence\current\03-preview-resources.json`

**Interfaces:**
- Consumes: guarded scripts and runbooks from Tasks 1-2.
- Produces: isolated free preview resources and redacted IDs for the final rollout record.

This task intentionally creates no repository diff; do not make an empty commit.

**External mutations:** staging Airtable, Cloudinary prefix, Upstash database, and
Vercel Preview environment variables.

**Evidence:** resource IDs, environment key names, and redacted screenshots or API
status; never secret values.

- [ ] **Step 1: Announce and confirm the free-resource mutation gate**

State that this task creates one Upstash Free database, one synthetic Airtable
staging/restore target, namespaced Cloudinary pending paths, and preview-only
Vercel variables. Stop if any provider requests billing or a card.

- [ ] **Step 2: Create or select a staging Airtable base**

Use the existing account only. Confirm its base ID differs from production. Create
the normalized schema through `python -m ops.cli preflight --target staging` and
the guarded schema command. Seed synthetic names only, such as `Fixture Parent A`,
never production biographies, contacts, or submissions.

The staging schema includes `PersonVersions`, `FamilyUnits`, `ParentChildLinks`,
`ChangeLog`, `GraphCommits`, `GraphState`, `EnrichmentAttempts`, and
`SubmissionReviews`, plus stable-ID fields on the existing staging person table.

If the PAT lacks schema scope, stop and request the minimum schema permission;
do not broaden unrelated account access.

- [ ] **Step 3: Create Upstash Free through Vercel integration**

Select the free plan and one database. Connect it to the backend Vercel project.
Create strict namespaces `preview:shajra` and `production:shajra`; no family data
is stored. Verify `PING`, lease acquire/release, and rate-limit scripts with the
preview namespace.

- [ ] **Step 4: Configure Cloudinary preview isolation**

Use the existing account and server-side signed upload. Set preview prefix
`shajra/preview` and production prefix `shajra/production`. Do not make pending
uploads public and do not upload a production family image during verification.

- [ ] **Step 5: Set Preview environment variables only**

Backend Preview keys:

```text
APP_ENV
AIRTABLE_PAT
AIRTABLE_BASE_ID
GROQ_API_KEY
GROQ_MODEL
AI_PROMPT_VERSION
AI_ENRICHMENT_ENABLED
AI_TIMEOUT_SECONDS
ENRICHMENT_STALE_AFTER_SECONDS
CLOUDINARY_URL
CLOUDINARY_FOLDER_PREFIX
UPSTASH_REDIS_REST_URL
UPSTASH_REDIS_REST_TOKEN
REDIS_NAMESPACE
ADMIN_USERNAME
ADMIN_PASSWORD_HASH
JWT_SECRET
MUTATION_PREVIEW_SECRET
JWT_ISSUER
JWT_AUDIENCE
GOOGLE_WEBHOOK_SECRET
CORS_ALLOWED_ORIGINS
PUBLIC_WRITES_ENABLED
RELATIONSHIP_WRITES_ENABLED
NORMALIZED_READS_ENABLED
```

Use staging values, `APP_ENV=preview`, prefix/namespace `preview`, and all four
feature flags `false`. Frontend Preview needs server-only fallback `BACKEND_API_URL`; the
Related Projects value takes precedence on Git previews.

- [ ] **Step 6: Run staging provider contracts**

```powershell
cd D:\andrew\shajra-api\backend
$env:APP_ENV='preview'
python -m pytest tests/integration -m integration -q
```

Expected: synthetic staging tests pass, production-base guard passes, and cleanup
archives only run-specific synthetic records.

### Task 4: Configure Vercel Roots and Create Matched Git Previews

**Files:**
- Write redacted external evidence only: `D:\shajra-rollout-evidence\current\04-preview-deployments.json`

**Interfaces:**
- Consumes: preview resources and dry-run Vercel scripts.
- Produces: correct project roots, one PR, and matched backend/frontend preview IDs.

Only implementation commits created by prior plans are pushed; this task creates
no evidence-only Git commit.

**External mutations:** Vercel project settings, GitHub feature branch/PR, preview deployments.

- [ ] **Step 1: Audit immediately before applying**

Run:

```powershell
pwsh scripts/vercel/audit-projects.ps1
pwsh scripts/vercel/configure-projects.ps1
```

Save the redacted audit output under the task log, outside Git.

- [ ] **Step 2: Apply only the reviewed root changes**

Run:

```powershell
pwsh scripts/vercel/configure-projects.ps1 -Apply
pwsh scripts/vercel/audit-projects.ps1
```

Expected roots: backend `backend`, frontend `frontend`. Verify no production
deployment was triggered solely by the settings PATCH.

- [ ] **Step 3: Push the implementation branch and create a PR**

Use a branch name `codex/shajra-reliability`. Run the full local gate, then:

```powershell
git push -u origin codex/shajra-reliability
gh pr create --base main --head codex/shajra-reliability --title "Rebuild Shajra graph and Vercel deployment" --body-file docs/runbooks/shajra-deployment.md
```

The push should trigger one backend and one frontend Git preview for the same
commit. Related Projects must point frontend preview server calls to that backend
preview.

- [ ] **Step 4: Wait for both previews without promoting**

Record backend and frontend deployment IDs, commit SHA, branch URLs, build status,
and build logs. Backend must reach READY before frontend end-to-end verification.
Do not promote either deployment in this task.

### Task 5: Verify the Preview End to End

**Files:**
- Write redacted external evidence only: `D:\shajra-rollout-evidence\current\05-preview-verification.json`

**Interfaces:**
- Consumes: matched previews and synthetic staging resources.
- Produces: read, write, graph, UX, accessibility, and AI preview evidence.

This task intentionally creates no repository diff; do not make an empty commit.

**External mutations:** only synthetic staging writes after read-only preview passes.

- [ ] **Step 1: Run read-only backend smoke tests**

Set `$API` to the related backend preview URL discovered from Vercel metadata and run:

```powershell
curl.exe -fsS "$API/api/health/live"
curl.exe -fsS "$API/api/health/ready"
curl.exe -fsS "$API/api/members"
curl.exe -fsS "$API/api/v2/tree"
curl.exe -fsS "$API/api/map-markers"
curl.exe -sS -o NUL -w "%{http_code}" "$API/api/admin/pending"
```

Expected final code: `401`. Health reports all writes disabled and normalized reads
disabled until the staging migration completes.

- [ ] **Step 2: Run fixture-backed frontend browser tests against preview**

Run Playwright's route, graph geometry, accessibility, and no-Railway tests at the
frontend preview. Capture screenshots at 390x844, 768x1024, and 1440x900. Assert
browser console has no errors and network logs contain no Railway requests.

- [ ] **Step 3: Run the staging migration and normalized-read preview**

Back up the synthetic staging source, restore to the staging normalized tables,
verify exact counts/checksum, then set only Preview
`NORMALIZED_READS_ENABLED=true`. Redeploy the same branch and rerun backend and
frontend read-only checks.

- [ ] **Step 4: Enable preview writes and test all four write classes**

Set Preview public and relationship flags `true`. Use synthetic identities only.
Test raw submission plus media, comments/stories, a valid relationship preview and
commit, stale revision `409`, compensation, login throttling, webhook signature,
and rate limits. Re-run migration verification afterward.

- [ ] **Step 5: Test review-only AI with synthetic data**

Set Preview `AI_ENRICHMENT_ENABLED=true` and use a synthetic pending submission
whose people are named `Fixture Person A/B`. Trigger enrichment from the admin UI,
verify bounded candidate IDs, field-level evidence/decisions, retryable failure
display, and that accepting a suggestion returns only a mutation draft. Network
logs must show no graph mutation until the separately previewed synthetic command
is confirmed. Record attempt ID, model label, prompt version, and status only.

- [ ] **Step 6: Return preview flags to safe values**

Set Preview public writes, relationship writes, and AI enrichment back to `false`,
leave normalized reads `true`, redeploy, and verify health reflects the change.

### Task 6: Promote Production Code with Writes Disabled

**Files:**
- Write redacted external evidence only: `D:\shajra-rollout-evidence\current\06-production-code.json`

**Interfaces:**
- Consumes: verified preview deployment IDs.
- Produces: backend-first production code with every mutation/AI flag disabled.

This task intentionally creates no repository diff; do not make an empty commit.

**External mutations:** production Vercel environment and backend/frontend promotion.

- [ ] **Step 1: Configure production environment without changing data**

Backend production uses current production Airtable/Cloudinary/Groq values,
production Upstash namespace, distinct strong JWT, mutation-preview, and webhook
secrets, and exact origins.
Set:

```text
APP_ENV=production
PUBLIC_WRITES_ENABLED=false
RELATIONSHIP_WRITES_ENABLED=false
NORMALIZED_READS_ENABLED=false
AI_ENRICHMENT_ENABLED=false
```

Frontend production sets:

```text
NEXT_PUBLIC_API_URL=https://backend-one-xi-26.vercel.app
BACKEND_API_URL=https://backend-one-xi-26.vercel.app
```

- [ ] **Step 2: Promote the verified backend Git preview first**

Use the backend preview deployment ID from Task 4 and Vercel's promotion command
or API. Wait for READY. Verify health, v1 read endpoints, v2 endpoint availability,
401 admin path, and all three flags. Do not promote frontend if any check fails.

- [ ] **Step 3: Promote the verified frontend Git preview second**

Wait for READY, verify every public route, error states, browser console, network
logs, and responsive screenshots. Forms must show the controlled disabled-write
response without losing entered values. The old production deployment IDs remain
recorded for instant rollback.

- [ ] **Step 4: Observe before data migration**

For at least 15 minutes, check Vercel function errors, 5xx rates, Airtable rate
limits, Upstash errors, and Cloudinary requests. Read-only requests must remain
healthy and no new production Airtable rows may appear.

### Task 7: Production Backup, Restore Drill, and Migration Review

**Files:**
- Write encrypted backup/artifacts outside Git under `D:\shajra-backups\`.
- Write redacted evidence only: `D:\shajra-rollout-evidence\current\07-migration-review.json`

**Interfaces:**
- Consumes: production read access and staging restore target.
- Produces: encrypted backup, reviewed migration plan, and explicit apply gate.

This task intentionally creates no repository diff; do not make an empty commit.

**External mutations:** encrypted local backup and staging restore only. Production remains read-only.

- [ ] **Step 1: Run production read-only preflight**

```powershell
cd D:\andrew\shajra-api\backend
python -m ops.cli preflight --source production --read-only
```

Record current counts and all issue codes. Expect 27 approved people only if the
live count has not changed; use the actual preflight count as authority.

- [ ] **Step 2: Create an encrypted backup outside the repository**

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "D:\shajra-backups\$stamp\snapshot.sbk"
New-Item -ItemType Directory -Force -Path (Split-Path $backup) | Out-Null
python -m ops.cli backup --source production --output $backup
```

Verify the `.sha256`, file permissions, successful decrypt in memory, and that
`git status --short` shows no backup artifact.

- [ ] **Step 3: Generate audit and deterministic migration plan**

```powershell
$artifactDir = "D:\shajra-backups\$stamp\artifacts"
New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
python -m ops.cli audit --backup $backup --output "$artifactDir\audit.json"
python -m ops.cli plan --backup $backup --output "$artifactDir\plan.json"
```

Summarize counts, checksum, exact-ID relationships, ambiguities, and unresolved
names to the user without exposing contact data or full biographies.

- [ ] **Step 4: Restore and verify in staging**

```powershell
python -m ops.cli restore --backup $backup --target staging --apply
python -m ops.cli verify --target staging --plan "$artifactDir\plan.json"
```

Run the complete preview graph and form suite against the restored staging data.

- [ ] **Step 5: Stop for explicit production migration approval**

Present the migration report, current ambiguities, backup checksum, staging
verification, rollback procedure, and exact production write count. Do not run
production `migrate` until the user explicitly approves this reviewed report.

### Task 8: Apply Migration and Enable Normalized Reads

**Files:**
- Write redacted external evidence only: `D:\shajra-rollout-evidence\current\08-production-migration.json`

**Interfaces:**
- Consumes: exact approved plan SHA and drift-free production preflight.
- Produces: verified normalized production revision with reversible read cutover.

This task intentionally creates no repository diff; do not make an empty commit.

**External mutations:** normalized production Airtable rows and backend production flag.

- [ ] **Step 1: Re-run read-only preflight and detect drift**

If production counts or checksum differ from Task 7, discard the migration plan,
take a new backup, restore it to staging, and request approval again.

- [ ] **Step 2: Apply the exact reviewed plan**

```powershell
$plan = "$artifactDir\plan.json"
$planSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $plan).Hash.ToLowerInvariant()
python -m ops.cli migrate --target production --plan $plan --apply --confirm-sha $planSha
python -m ops.cli verify --target production --plan $plan
```

Expected: actual counts equal plan counts, all blocking issues absent, semantic
checksum exact, and legacy people unchanged.

- [ ] **Step 3: Enable normalized reads only**

Set production `NORMALIZED_READS_ENABLED=true` while both write flags remain
`false`. Redeploy/promote backend, verify v2 checksum and person accounting, then
verify frontend graph geometry and every route.

- [ ] **Step 4: Prove read rollback**

In staging, toggle normalized reads off, verify legacy adapter, then restore it to
on. In production, retain the flag rollback command and previous deployment ID;
do not deliberately degrade the healthy production site solely for a drill.

### Task 9: Enable Validated Writes in Two Stages

**Files:**
- Write redacted external evidence only: `D:\shajra-rollout-evidence\current\09-production-writes.json`

**Interfaces:**
- Consumes: verified normalized production reads and coordination health.
- Produces: deliberately enabled public then relationship writes with monitoring evidence.

This task intentionally creates no repository diff; do not make an empty commit.

**External mutations:** production environment flags and normal user write capability.

- [ ] **Step 1: Enable public non-relationship writes**

Set `PUBLIC_WRITES_ENABLED=true`, keep relationship writes false, redeploy backend,
and verify health. Test invalid no-write submissions and rate limits. A successful
production submission/media test requires explicit approval at this moment or a
real user action supplied by the owner.

- [ ] **Step 2: Enable relationship writes**

Confirm Upstash production lease, fencing, revocation, and rate-limit checks pass.
Set `RELATIONSHIP_WRITES_ENABLED=true`, redeploy, and verify health. Test admin
login and read-only snapshot. A successful production graph mutation requires an
owner-reviewed real edit, never a synthetic family member.

- [ ] **Step 3: Monitor and preserve rollback evidence**

Observe for at least 30 minutes: Vercel errors, Airtable `429`, Upstash failures,
Cloudinary errors, graph checksum drift, pending operations, and frontend console
errors. Keep old deployment IDs and migration backup until the retention policy
allows removal.

### Task 10: Enable Review-Only AI Enrichment

**Files:**
- Write redacted external evidence only: `D:\shajra-rollout-evidence\current\10-production-ai.json`

**Interfaces:**
- Consumes: synthetic preview proof and owner approval for first production use.
- Produces: enabled review-only enrichment, first-attempt evidence, and tested kill switch.

This task intentionally creates no repository diff; do not make an empty commit.

**External mutations:** production environment flag and potential Groq usage.

- [ ] **Step 1: Recheck preview evidence and free-tier status**

Confirm the synthetic preview attempt passed strict schema, privacy, candidate,
failure, manual-review, and no-direct-write assertions. Confirm the configured Groq
account/model does not require billing for the intended use. Stop if billing or a
card is requested.

- [ ] **Step 2: Enable the production pipeline without invoking it**

Set `AI_ENRICHMENT_ENABLED=true`, keep the server-managed prompt/model values,
redeploy backend, and verify health/integration status reports enabled and
configured without exposing secrets or prompt text. Do not submit production PII
or trigger a provider call for this health check.

- [ ] **Step 3: Gate the first production enrichment**

The first provider call must use an owner-selected real pending submission and
requires explicit approval at that moment. Before approval, show exactly which
public candidate fields will be sent. Afterward, verify the attempt remains a
suggestion, the admin can review manually, and the graph checksum is unchanged.

- [ ] **Step 4: Monitor and retain the kill switch**

Observe provider errors, latency, invalid-schema codes, attempts per submission,
and unexpected graph checksum changes for 30 minutes after the first approved use.
Set `AI_ENRICHMENT_ENABLED=false` immediately if privacy, schema, or direct-write
invariants fail; public submissions and manual admin review must continue working.

### Task 11: Merge, Remove Railway Configuration, Tag, and Rotate Credential

**Files:**
- Modify: `docs/runbooks/shajra-deployment.md`, `shajra-migration.md`, `shajra-rollback.md`
- Read and sanitize: `D:\shajra-rollout-evidence\current\*.json`
- Delete from repository: `railway.json`, root `Procfile`, root `requirements.txt`, and `backend/Procfile`.

**Interfaces:**
- Consumes: all redacted gate evidence and verified production state.
- Produces: sanitized permanent record, merged `main`, release tag, and revoked exposed token.

**External mutations:** repository main branch, production redeploy from merge, release tag, token rotation.

- [ ] **Step 1: Review and merge the PR**

Require all CI and Vercel checks. Review the diff for credentials, backups,
migration artifacts, private submissions, and production data. Merge only after
production is already running the verified compatible backend and frontend.

- [ ] **Step 2: Verify merge-triggered deployments**

Both projects may deploy the merge commit. Wait for backend and frontend READY,
then rerun read-only smoke, graph geometry, and no-Railway checks. Roll back the
individual project if its merge deployment regresses.

- [ ] **Step 3: Remove obsolete Railway repository files in a separate commit**

```powershell
git rm railway.json Procfile requirements.txt backend/Procfile
git commit -m "chore: remove retired Railway deployment files"
git push origin main
```

Do not delete the Railway cloud project as part of this command.

- [ ] **Step 4: Rotate the Vercel token and re-audit**

Revoke the credential shared in chat, create a replacement only if ongoing
automation needs it, update secure local/Vercel storage, and run the read-only
project audit with the replacement. Never paste the replacement into chat or a
repository file.

- [ ] **Step 5: Commit the sanitized final verification record**

Read every external evidence JSON, reject any secret-like key/value, and copy only
the approved fields into the three runbooks. Record commit and planned tag,
backend/frontend deployment IDs, migration run ID, backup and
semantic checksums, graph counts, unresolved relationship count, test commands,
viewport screenshots, approved first enrichment attempt ID, prompt/model labels,
monitoring outcome, and rollback references in the runbook. Do not include private
data, model input/output, or credentials.

```powershell
git add docs/runbooks
git commit -m "docs: record verified Shajra v2 rollout"
git push origin main
```

- [ ] **Step 6: Tag the recorded release**

After the documentation commit deploys and the final production checks pass:

```powershell
git tag -a shajra-v2.0.0 -m "Shajra normalized graph and Vercel rollout"
git push origin shajra-v2.0.0
```

## Completion Gate

The rollout is complete only when the free Vercel deployments are healthy, Railway
is absent from runtime traffic, every approved person and primary relationship is
accounted for, the graph geometry, admin UX, AI review, and accessibility gates
pass, migration and rollback evidence exists, validated writes and AI enrichment
are enabled deliberately, GitHub main and the release tag match production, and
the exposed Vercel credential is revoked.
