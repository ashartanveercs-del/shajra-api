# Shajra Frontend Graph and Forms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a deterministic family graph, coherent public experience, reliable submissions, and a fast revision-safe admin and AI-review workspace on top of the normalized v2 API.

**Architecture:** Split the frontend into typed API, graph, submission, admin, enrichment-review, and shared interaction layers. React Flow renders custom person and family-unit nodes; ELK computes deterministic layered geometry with fixed ports. Public writes and admin authentication use same-origin Next.js route handlers, while the browser never stores a bearer token. Admin drag/drop creates drafts only; AI suggestions enter the same previewed mutation workflow as manual edits.

**Tech Stack:** Next.js 16.2.12, React 19.2.8, TypeScript, React Aria Components 1.20.0, React Flow 12.11.2, ELK.js 0.12.0, Zod 4.4.3, React Hook Form 7.84.0, Hookform Resolvers 5.7.1, Vitest 4.1.10, Playwright 1.62.1, Axe Playwright 4.12.1.

## Global Constraints

- Complete the platform, graph-core, and backend persistence plans first.
- The frontend never infers, heals, or persists a family relationship itself.
- Every person has one primary graph node; other appearances are labelled references.
- Person nodes are 184x96 CSS pixels; family-unit nodes are 44x44 CSS pixels.
- Input IDs, ELK nodes, ports, edges, and output coordinates are stable and ID-sorted.
- Use React Flow custom nodes/edges and ELK layered layout; do not revive nested-list tree CSS.
- Use Lucide icons with tooltips for graph controls and 8px-or-less radii for cards and nodes.
- Preserve explicit loading, empty, partial, unavailable, and ready states.
- The list fallback contains every active person and is keyboard accessible.
- Admin tokens are secure HttpOnly cookies and never enter `localStorage` or client state.
- Admin drag/drop, AI decisions, and inspector edits create drafts only; only the
  existing preview-and-confirm mutation path can write graph state.
- Use actual family content and member media as the visual signal; add no generic
  marketing hero, decorative orb, gradient background, or nested card layout.
- No production form submission or admin mutation is used during tests.
- Every task ends with focused tests and a local commit.

---

## File Structure

Create:

- `frontend/src/lib/api/types.ts`, `http.ts`, `public.ts`, `admin.ts`
- `frontend/src/lib/server/backend.ts`, `admin-proxy.ts`, `origin-check.ts`
- `frontend/src/lib/auth/session.ts`
- `frontend/src/lib/validation/dates.ts`, `submission.ts`
- `frontend/src/components/ui/`: accessible controls, dialogs, feedback, and status primitives.
- `frontend/src/components/shell/AppShell.tsx`, `PublicNav.tsx`
- `frontend/src/app/api/auth/login/route.ts`, `logout/route.ts`, `session/route.ts`
- `frontend/src/app/api/admin/[...path]/route.ts`
- `frontend/src/app/api/public/submissions/route.ts`
- `frontend/src/app/api/public/submissions/[submissionId]/media/route.ts`
- `frontend/src/features/tree/types.ts`, `state.ts`, `use-tree-projection.ts`
- `frontend/src/features/tree/layout/elk-graph.ts`, `layout-tree.ts`, `layout-hash.ts`, `branch.ts`
- `frontend/src/features/tree/components/FamilyGraph.tsx`, `PersonNode.tsx`
- `frontend/src/features/tree/components/FamilyUnitNode.tsx`, `RelationshipEdge.tsx`
- `frontend/src/features/tree/components/TreeToolbar.tsx`, `TreeListView.tsx`
- `frontend/src/features/tree/components/TreeStateView.tsx`, `GraphHealthBanner.tsx`
- `frontend/src/features/submissions/types.ts`, `api.ts`
- Submission components: `SubmissionForm`, `RelationFields`, `PersonCombobox`,
  `DateField`, `ImageField`, and `ErrorSummary`.
- `frontend/src/features/admin/types.ts`, `api.ts`, `relationship-draft.ts`
- `frontend/src/features/admin/workspace-state.ts`, `unsaved-changes.ts`
- Admin components: `AdminLogin`, `AdminWorkspace`, `AdminToolbar`,
  `AdminNavigator`, `GraphInspector`, `RelationshipEditor`, `MutationPreview`,
  `GraphHealthPanel`, `ArchivePersonDialog`, `AuditLog`, and `IntegrationStatus`.
- `frontend/src/features/enrichment/types.ts`, `api.ts`, `review-state.ts`
- Enrichment components: `SubmissionQueue`, `SubmissionReviewWorkspace`,
  `SubmissionComparison`, `SuggestionDecisionRow`, and `AttemptTimeline`.
- `frontend/src/test/fixtures/tree.ts`, `admin.ts`
- Unit and component tests beside each feature.
- `frontend/playwright.config.ts`
- `frontend/e2e/tree-geometry.spec.ts`, `tree-accessibility.spec.ts`
- `frontend/e2e/submission.spec.ts`, `admin-preview.spec.ts`

Modify:

- `frontend/package.json`, `frontend/package-lock.json`
- `frontend/src/lib/api.ts`: compatibility barrel only.
- `frontend/src/app/layout.tsx`, `globals.css`
- `frontend/src/app/tree/page.tsx`, `submit/page.tsx`, `admin/page.tsx`
- Home, member, search, and map pages.
- `frontend/src/components/Navbar.tsx`, `GlobeMap.tsx`

Delete after replacement:

- `frontend/src/components/AdminTreeEditor.tsx`

## Interfaces

```ts
export type TreeStatus = "ready" | "empty" | "partial" | "unavailable";

export interface TreeProjectionV2 {
  schemaVersion: "2";
  revision: number;
  status: TreeStatus;
  people: PublicPerson[];
  familyUnits: PublicFamilyUnit[];
  parentChildLinks: PublicParentChildLink[];
  references: RelationshipReference[];
  components: GraphComponent[];
  issues: GraphIssue[];
  unresolvedCount: number;
}

export function fetchTreeProjection(signal?: AbortSignal): Promise<TreeProjectionV2>;
export function toElkGraph(tree: TreeProjectionV2): ElkNode;
export function layoutTree(tree: TreeProjectionV2): Promise<LayoutResult>;
export function hashLayout(layout: LayoutResult): Promise<string>;
```

### Task 1: Install Graph, Form, and Browser-Test Dependencies

**Files:**
- Modify: `frontend/package.json`, `frontend/package-lock.json`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/src/test/fixtures/tree.ts`
- Create: `frontend/src/test/fixtures/admin.ts`

**Interfaces:**
- Produces: dependencies and fixture contracts for every later frontend task.

- [ ] **Step 1: Install exact runtime packages**

Run:

```powershell
cd D:\andrew\shajra-api\frontend
npm install --save-exact react-aria-components@1.20.0 @xyflow/react@12.11.2 elkjs@0.12.0 zod@4.4.3 react-hook-form@7.84.0 @hookform/resolvers@5.7.1
npm install --save-dev --save-exact @playwright/test@1.62.1 @axe-core/playwright@4.12.1 pngjs@7.0.0 @types/pngjs@6.0.5
npx playwright install chromium
```

Add scripts:

```json
{
  "test:e2e": "playwright test",
  "test:e2e:update": "playwright test --update-snapshots"
}
```

- [ ] **Step 2: Configure Playwright without production writes**

Write `frontend/playwright.config.ts`:

```ts
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:3000",
    reuseExistingServer: true,
  },
  projects: [
    { name: "mobile", use: { ...devices["iPhone 13"] } },
    { name: "tablet", use: { viewport: { width: 768, height: 1024 } } },
    { name: "desktop", use: { viewport: { width: 1440, height: 900 } } },
  ],
});
```

Tests use route interception or a fixture backend. They must abort any unexpected
POST, PUT, PATCH, or DELETE request.

- [ ] **Step 3: Create normalized fixtures with literal IDs**

`tree.ts` exports `emptyTree`, `twoParentTree`, `remarriageTree`,
`repeatedAncestorTree`, `disconnectedTree`, and `partialTree`. Use literal IDs
such as `per_anna`, `fam_anna_ben`, and `lnk_anna_child`; never random IDs.

`admin.ts` exports revision `7`, one valid mutation preview, one cycle rejection,
and one `409` stale-revision problem.

- [ ] **Step 4: Verify and commit**

```powershell
npm run typecheck
npm test
npx playwright test --list
git add frontend/package.json frontend/package-lock.json frontend/playwright.config.ts frontend/src/test
git commit -m "test: add Shajra graph and browser tooling"
```

### Task 2: Split the Typed v2 API Boundary

**Files:**
- Create: `frontend/src/lib/api/types.ts`, `http.ts`, `public.ts`, `admin.ts`
- Create: tests beside those modules.
- Modify: `frontend/src/lib/api.ts`

**Interfaces:**
- Produces: public v2 DTOs and same-origin admin API functions.

- [ ] **Step 1: Write failing DTO parsing tests**

Use Zod schemas to reject Airtable IDs, private fields, unknown schema versions,
and missing graph revision. The core assertion is:

```ts
expect(() => treeProjectionSchema.parse({ ...twoParentTree, schemaVersion: "3" }))
  .toThrow();
expect(JSON.stringify(treeProjectionSchema.parse(twoParentTree))).not.toContain("Email");
```

- [ ] **Step 2: Define branded IDs and DTO schemas**

In `types.ts`:

```ts
export const personIdSchema = z.string().regex(/^per_[a-z0-9_]+$/).brand<"PersonId">();
export const familyUnitIdSchema = z.string().regex(/^fam_[a-z0-9_]+$/).brand<"FamilyUnitId">();
export const linkIdSchema = z.string().regex(/^lnk_[a-z0-9_]+$/).brand<"LinkId">();
export const submissionIdSchema = z.string().regex(/^sub_[a-z0-9_]+$/).brand<"SubmissionId">();
export const attemptIdSchema = z.string().regex(/^att_[a-z0-9_]+$/).brand<"AttemptId">();
export const reviewIdSchema = z.string().regex(/^rev_[a-z0-9_]+$/).brand<"ReviewId">();

export type PersonId = z.infer<typeof personIdSchema>;
export type FamilyUnitId = z.infer<typeof familyUnitIdSchema>;
export type LinkId = z.infer<typeof linkIdSchema>;
export type SubmissionId = z.infer<typeof submissionIdSchema>;
export type AttemptId = z.infer<typeof attemptIdSchema>;
export type ReviewId = z.infer<typeof reviewIdSchema>;
```

`MutationDraft` is `{ schemaVersion: "1"; snapshotRevision: number;
idempotencyKey: string; commands: GraphCommandDto[]; sourceReviewId?: ReviewId }`. Define
`GraphCommandDto` as a strict discriminated union for add/update person, create/
supersede family unit, create/supersede parent-child link, set primary placement,
archive person, and unresolved annotation. Its JSON field names map one-to-one to
the backend `GraphMutationRequest` command schemas.
The API mapper converts `sourceReviewId` to backend `source_reference`; only a
branded `ReviewId` is accepted.

Define strict schemas for partial dates, people, family units, links, references,
components, issues, tree projection, mutation draft, preview, result, and RFC 9457
problem details. Add submission review summary/detail, enrichment attempt,
candidate, suggestion, decision, and completed-review schemas matching backend
camel-case fields. Derive TypeScript types with `z.infer` rather than duplicating
interfaces. `PublicParentChildLink.primary` is required and comes from the backend
projection; no frontend schema default may invent it.

- [ ] **Step 3: Implement public client parsing**

`public.ts` calls `requestJson<unknown>`, then parses:

```ts
export async function fetchTreeProjection(signal?: AbortSignal) {
  const body = await requestJson<unknown>("/api/v2/tree", { signal, cache: "no-store" });
  return treeProjectionSchema.parse(body);
}
```

Add typed functions for members, member detail, search, map, comments, stories,
albums, submission, media status, admin submission review, enrichment attempts,
and review decisions. Public person functions accept only `PersonId`.

- [ ] **Step 4: Implement same-origin admin client signatures**

All admin calls target `/api/admin/...` on the frontend origin, set
`credentials: "same-origin"`, and never accept a token argument:

```ts
export function previewMutation(draft: MutationDraft): Promise<MutationPreview>;
export function commitMutation(preview: MutationPreview): Promise<MutationResult>;
export function fetchAdminSnapshot(): Promise<AdminGraphSnapshot>;
export function compensateOperation(operationId: string, revision: number): Promise<MutationResult>;
```

Commit sends the preview's idempotency key and revision through the proxy.

- [ ] **Step 5: Convert `src/lib/api.ts` into an explicit compatibility barrel**

Re-export public v2 clients and temporary v1 content types. Delete bearer-token
admin helpers. Compile failures identify old callers to migrate in later tasks.

- [ ] **Step 6: Run and commit**

```powershell
npm test -- src/lib/api
npm run typecheck
npm run lint
git add frontend/src/lib/api frontend/src/lib/api.ts
git commit -m "feat: add typed Shajra v2 frontend API"
```

### Task 3: Same-Origin Admin Session and Allowlisted Proxy

**Files:**
- Create: `frontend/src/lib/server/backend.ts`, `origin-check.ts`, `admin-proxy.ts`
- Create: `frontend/src/lib/auth/session.ts`
- Create: `frontend/src/app/api/auth/login/route.ts`, `logout/route.ts`, `session/route.ts`
- Create: `frontend/src/app/api/admin/[...path]/route.ts`
- Create: unit tests beside server modules and route tests.
- Modify: `frontend/next.config.ts`

**Interfaces:**
- Produces: secure cookie `shajra_admin_session` and allowlisted admin forwarding.

- [ ] **Step 1: Write failing origin and allowlist tests**

Assert login, logout, and proxy mutation requests with missing or foreign `Origin`
return `403`; GET is allowed; `/graph/preview`, `/graph/mutations`, `/operations`, `/pending`, and
`/integrations` are allowlisted. Also allow only the documented methods for
`/submissions`, `/submissions/{id}`, `/submissions/{id}/enrichment`,
`/submissions/{id}/enrichment-attempts`, and `/submissions/{id}/reviews`; path
traversal and arbitrary backend paths are rejected.

- [ ] **Step 2: Implement server-only backend URL resolution**

`backend.ts` reads `BACKEND_API_URL`, allows localhost only in development/test,
strips trailing slashes, and throws at startup in preview/production if absent.
Never import it into a client component.

- [ ] **Step 3: Implement secure session cookies**

Use this configuration:

```ts
export const sessionCookie = {
  name: "shajra_admin_session",
  options: {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax" as const,
    path: "/",
    maxAge: 15 * 60,
  },
};
```

Login forwards username/password to backend over server-side fetch, stores only
the returned access token in this cookie, and returns `{ authenticated: true }`.
Login and logout first apply the same exact-origin check as admin mutations. Logout
clears the cookie. Session reports only identity and expiry, never the token.

- [ ] **Step 4: Implement the admin proxy**

Map frontend paths to exact backend v2 paths and methods. For state-changing
methods, verify `Origin` equals the request URL origin. Forward the cookie token as
`Authorization: Bearer`, plus `Idempotency-Key`, `If-Match`, `X-Request-Id`, and
JSON body. Never forward `Cookie`, `Host`, arbitrary headers, or arbitrary paths.
Clear the cookie on backend `401`.

- [ ] **Step 5: Run and commit**

```powershell
npm test -- src/lib/server src/lib/auth src/app/api/auth src/app/api/admin
npm run typecheck
npm run lint
git add frontend/src/lib/server frontend/src/lib/auth frontend/src/app/api frontend/next.config.ts
git commit -m "feat: secure Shajra admin sessions"
```

### Task 4: Deterministic ELK Graph Model and Layout

**Files:**
- Create: `frontend/src/features/tree/types.ts`, `state.ts`
- Create: `frontend/src/features/tree/layout/elk-graph.ts`, `layout-tree.ts`
- Create: `frontend/src/features/tree/layout/layout-hash.ts`, `branch.ts`
- Create: tests beside layout modules.

**Interfaces:**
- Consumes: `TreeProjectionV2`.
- Produces: React Flow nodes, edges, deterministic positions, layout hash, branch ID set.

- [ ] **Step 1: Write failing model and determinism tests**

For all fixtures, assert every person ID maps to exactly one React Flow person
node, every family unit maps to one junction, edge handles exist, ten input-order
shuffles yield one layout hash after awaiting `Promise.all` over `hashLayout`, and
repeated ancestors create references rather than duplicate person nodes.

For `twoParentTree`, assert two adult-membership edges enter the family unit and
exactly one descendant edge leaves that unit for its child, despite two underlying
parent-child links.

- [ ] **Step 2: Define typed node and edge unions**

```ts
export type PersonFlowNode = Node<PersonNodeData, "person">;
export type FamilyUnitFlowNode = Node<FamilyUnitNodeData, "familyUnit">;
export type ShajraNode = PersonFlowNode | FamilyUnitFlowNode;
export type ShajraEdge = Edge<RelationshipEdgeData, "relationship">;

export const PERSON_NODE = { width: 184, height: 96 } as const;
export const FAMILY_UNIT_NODE = { width: 44, height: 44 } as const;
```

Use fixed ports `person-parent-out`, `family-adult-a-in`, `family-adult-b-in`,
`family-child-out`, and `person-child-in`. Edge IDs are semantic and stable.

- [ ] **Step 3: Convert v2 projection to ELK input**

Sort every person, family unit, and link by ID. Add one ELK node per person and
family unit. Derive adult edges from `adultAId`/`adultBId`, with IDs
`adult:{familyUnitId}:{personId}`. Group primary links by
`(familyUnitId, childId)` and emit one `child:{familyUnitId}:{childId}` edge from
the family child port. A primary link without a family unit emits
`parent:{parentId}:{childId}`. Reference edges are excluded from ELK and overlaid
after layout. Reject conflicting grouped roles instead of hiding them.

Use these options:

```ts
const layoutOptions = {
  "elk.algorithm": "layered",
  "elk.direction": "DOWN",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.portConstraints": "FIXED_ORDER",
  "elk.spacing.nodeNode": "48",
  "elk.layered.spacing.nodeNodeBetweenLayers": "72",
  "elk.randomSeed": "17",
};
```

- [ ] **Step 4: Convert ELK output to React Flow**

Reject missing coordinates or ports. Round coordinates to two decimal places.
Preserve ELK edge sections for custom orthogonal paths. Add labelled dashed
reference edges after layout. `hashLayout` serializes sorted node IDs, rounded
positions, dimensions, edge IDs, and bend points before asynchronously returning
SHA-256 from Web Crypto.

- [ ] **Step 5: Implement branch selection**

`getBranchNodeIds(tree, personId)` returns the selected person, their primary
descendant family units, descendants, and labelled references, with a visited set
to prevent repeated traversal. It never changes layout positions.

- [ ] **Step 6: Run and commit**

```powershell
npm test -- src/features/tree/layout
npm run typecheck
npm run lint
git add frontend/src/features/tree
git commit -m "feat: add deterministic Shajra ELK layout"
```

### Task 5: Shared Application Shell and Accessible Interaction Primitives

**Files:**
- Create: `frontend/src/components/ui/IconButton.tsx`, `SegmentedControl.tsx`
- Create: `frontend/src/components/ui/Field.tsx`, `Combobox.tsx`, `Dialog.tsx`
- Create: `frontend/src/components/ui/StatusBadge.tsx`, `ToastRegion.tsx`, `ErrorSummary.tsx`
- Create: `frontend/src/components/shell/AppShell.tsx`, `PublicNav.tsx`
- Create: tests beside every primitive and shell component.
- Modify: `frontend/src/app/layout.tsx`, `frontend/src/app/globals.css`
- Modify: `frontend/src/components/Navbar.tsx`

**Interfaces:**
- Consumes: React Aria Components and Lucide icons.
- Produces: shared controls used by public, submission, graph, admin, and AI-review tasks.

- [ ] **Step 1: Write failing interaction and shell tests**

Test that every icon button has an accessible name and hover/focus tooltip;
segmented controls expose one selected value; comboboxes support keyboard search
and retain stable IDs; dialogs trap focus and restore it; error summaries link to
fields; toast messages use `aria-live="polite"`; active navigation uses
`aria-current="page"`; and the mobile menu is keyboard operable.

Include the stable-control assertion:

```tsx
render(<IconButton label="Fit tree"><ScanSearch aria-hidden="true" /></IconButton>);
const button = screen.getByRole("button", { name: "Fit tree" });
expect(button).toHaveClass("size-9");
await user.hover(button);
expect(await screen.findByRole("tooltip", { name: "Fit tree" })).toBeVisible();
```

- [ ] **Step 2: Replace the parchment theme with balanced application tokens**

Define these roots in `globals.css` and map them through Tailwind theme tokens:

```css
:root {
  --page: #f5f7f6;
  --surface: #ffffff;
  --surface-subtle: #edf2ef;
  --text: #1d2420;
  --text-muted: #5f6b64;
  --border: #d4ddd7;
  --primary: #176447;
  --primary-hover: #0f5038;
  --info: #2f6f9f;
  --accent: #7c4e70;
  --warning: #8a6518;
  --danger: #aa3838;
  --focus: #2563a6;
  --radius: 6px;
  --app-header-height: 56px;
}
```

Remove dominant beige/brown variables, gradients, decorative shadows, negative
letter spacing, and viewport-scaled fonts. Use radius `6px` for controls and at
most `8px` for repeated records/dialogs. Define fixed 36px icon buttons, 40px form
controls, visible `:focus-visible`, reduced-motion behavior, and text wrapping that
cannot overflow controls.

- [ ] **Step 3: Implement accessible primitives**

Wrap React Aria `Button`, `Tooltip`, `Tabs`, `ComboBox`, `Modal`, and `Dialog` in
the exact shared components above. `Combobox<T>` requires `getKey(item): string`
and never uses display text as identity. `ToastRegion` keeps at most three messages
and exposes success, info, warning, and error variants without blocking focus.

- [ ] **Step 4: Build the application shell and navigation**

`AppShell` renders one stable header and skip link. Its `contentWidth` prop is
exactly `"content" | "full"` and `showFooter` is boolean; Tree and Admin use
`full`, Admin sets `showFooter={false}`, and reading/form routes use `content` with
the footer. `PublicNav` links Home, Tree, People, Map, Stories, and Contribute; it uses
Lucide icons where compact and text labels where navigation meaning requires them.
The first viewport exposes actual Shajra content or tools, not feature marketing.
Replace `Navbar.tsx` with a compatibility re-export until callers migrate.

- [ ] **Step 5: Run and commit**

```powershell
npm test -- src/components/ui src/components/shell
npm run typecheck
npm run lint
git add frontend/src/components frontend/src/app/layout.tsx frontend/src/app/globals.css
git commit -m "feat: add Shajra application UI foundation"
```

### Task 6: Public Graph Canvas, States, and List Fallback

**Files:**
- Create: `frontend/src/features/tree/components/FamilyGraph.tsx`, `PersonNode.tsx`
- Create: `frontend/src/features/tree/components/FamilyUnitNode.tsx`, `RelationshipEdge.tsx`
- Create: `frontend/src/features/tree/components/TreeToolbar.tsx`, `TreeListView.tsx`
- Create: `frontend/src/features/tree/components/TreeStateView.tsx`, `GraphHealthBanner.tsx`
- Create: `frontend/src/features/tree/components/TreeInspector.tsx`, `RelationshipFilters.tsx`
- Create: `frontend/src/features/tree/use-tree-projection.ts`
- Create: component tests beside each component.
- Modify: `frontend/src/app/tree/page.tsx`, `globals.css`, `layout.tsx`

**Interfaces:**
- Consumes: layout output and tree DTOs.
- Produces: complete `/tree` experience.

- [ ] **Step 1: Write state and accounting tests**

Render loading, unavailable, empty, partial, and ready fixtures. Assert the list
fallback contains every person, partial state shows unresolved count, retry calls
refetch, and unavailable state contains no empty graph canvas. Selecting a person
writes `?person=per_value` with `router.replace`, a valid URL selection restores
the inspector after reload, and an unknown ID is removed without changing layout.

- [ ] **Step 2: Implement custom nodes and edges**

`PersonNode` renders image or initial, full name, lifespan, and selection state in
a fixed 184x96 box. `FamilyUnitNode` is a fixed 44x44 junction with a heart or
single-parent icon and accessible name. `RelationshipEdge` renders ELK orthogonal
sections and labelled reference style. Handles align exactly with ELK ports.

- [ ] **Step 3: Implement toolbar and fit behavior**

Use Lucide `ZoomIn`, `ZoomOut`, `Scan`, `Focus`, and `List` icon buttons with
tooltips and `aria-label`. Controls occupy stable 40x40 cells. Add fit-to-tree,
fit-to-selected-branch, zoom, reset, and canvas/list segmented mode. Preserve the
selected person across zoom and fit actions.

- [ ] **Step 4: Implement selection, filters, and inspector**

Use `PersonId` URL state and keep graph geometry immutable when selection changes.
Filters show biological, adoptive, step, guardian, and reference edges without
removing person nodes. `TreeInspector` shows photograph, lifespan, branch, family
units, and labelled references with one link to `/member/{personId}`. Render it as
an unframed right pane at desktop and an accessible bottom sheet below 768px.

- [ ] **Step 5: Implement `FamilyGraph` and state view**

Use React Flow with nodes non-connectable on the public page, pan/zoom enabled,
`fitView`, and no minimap on mobile. Await layout before rendering the canvas.
`TreeStateView` owns the five explicit states. `GraphHealthBanner` shows sanitized
warnings without private IDs or admin-only detail.

- [ ] **Step 6: Implement the semantic list fallback**

Group people by component and generation when available. Every person is a normal
link to `/member/{personId}`. Include spouses/family-unit labels and reference
relationships in text. The list remains rendered but visually hidden in canvas
mode so assistive technology can traverse it.

- [ ] **Step 7: Replace the old tree page and CSS**

Delete nested `ul/li` connector CSS and inline `dangerouslySetInnerHTML`. The page
loads through `useTreeProjection`, provides retry, and renders only feature
components. Import `@xyflow/react/dist/style.css` once from the root layout.

- [ ] **Step 8: Run and commit**

```powershell
npm test -- src/features/tree/components src/app/tree
npm run typecheck
npm run lint
git add frontend/src/features/tree frontend/src/app/tree frontend/src/app/globals.css frontend/src/app/layout.tsx
git commit -m "feat: rebuild the Shajra family graph"
```

### Task 7: Validated Public Submission and Media Flow

**Files:**
- Create: `frontend/src/lib/validation/dates.ts`, `submission.ts`
- Create: `frontend/src/features/submissions/types.ts`, `api.ts`
- Create: `frontend/src/features/submissions/components/SubmissionForm.tsx`
- Create: `frontend/src/features/submissions/components/SubmissionProgress.tsx`
- Create: `frontend/src/features/submissions/components/RelationFields.tsx`
- Create: `frontend/src/features/submissions/components/PersonCombobox.tsx`
- Create: `frontend/src/features/submissions/components/DateField.tsx`, `ImageField.tsx`
- Create: `frontend/src/features/submissions/components/ReviewSummary.tsx`
- Create: `frontend/src/app/api/public/submissions/route.ts`
- Create: `frontend/src/app/api/public/submissions/[submissionId]/media/route.ts`
- Create: tests beside validation, components, and route handlers.
- Modify: `frontend/src/app/submit/page.tsx`

**Interfaces:**
- Produces: raw-first pending submission with optional moderated image.

- [ ] **Step 1: Write schema tests**

Cover full-name trim and length, alive/deceased consistency, partial dates,
death-before-birth, email, phone, biography limit, explicit candidate IDs,
unresolved relation text, accepted MIME types, 5 MiB limit, and preserved values
after server failure.

- [ ] **Step 2: Implement Zod form schema**

Use a discriminated union on `lifeStatus`:

```ts
const livingSchema = baseSubmission.extend({
  lifeStatus: z.literal("living"),
  dateOfDeath: z.literal("").optional(),
});

const deceasedSchema = baseSubmission.extend({
  lifeStatus: z.literal("deceased"),
  dateOfDeath: partialDateInputSchema,
});

export const submissionSchema = z.discriminatedUnion("lifeStatus", [livingSchema, deceasedSchema]);
```

Add `superRefine` for chronology. Relation fields contain `{ candidateId,
unresolvedName }` and require at most one of them.

- [ ] **Step 3: Build accessible form controls**

Use React Hook Form with `zodResolver`. `PersonCombobox` queries stable-ID search,
supports keyboard selection, and offers `Not listed` without inventing an ID.
`DateField` captures year, month, or day precision. `ImageField` previews locally
but uploads only after raw submission returns a submission ID.

`SubmissionProgress` exposes Personal, Family, Media, and Review. At 768px and
above all sections remain in one document with a sticky progress rail; below 768px
one section is visible at a time. Next validates only the current section, Back
never clears values, and Review contains a plain-language summary with Edit links.

- [ ] **Step 4: Implement same-origin write route handlers**

The submission route forwards only allowlisted JSON fields to the backend. The
media route verifies the path submission ID and forwards multipart bytes without
buffering more than 5 MiB plus one byte. Neither route accepts a caller-supplied
backend URL or authorization header. Both reject a missing or foreign `Origin`
before reading the request body.

- [ ] **Step 5: Preserve errors and reset only on durable success**

Render an error summary linked to fields and inline errors. On `202`, show the
submission reference. Reset only after both submission and optional media upload
succeed; if media fails, retain the accepted submission reference and offer retry
for that media only.

- [ ] **Step 6: Run and commit**

```powershell
npm test -- src/lib/validation src/features/submissions src/app/api/public src/app/submit
npm run typecheck
npm run lint
git add frontend/src/lib/validation frontend/src/features/submissions frontend/src/app/api/public frontend/src/app/submit
git commit -m "feat: rebuild Shajra submissions safely"
```

### Task 8: Revision-Safe Admin Relationship Workspace

**Files:**
- Create: `frontend/src/features/admin/types.ts`, `api.ts`, `workspace-state.ts`
- Create: `frontend/src/features/admin/relationship-draft.ts`, `unsaved-changes.ts`
- Create: `frontend/src/features/admin/components/AdminLogin.tsx`, `AdminWorkspace.tsx`
- Create: `frontend/src/features/admin/components/AdminToolbar.tsx`, `AdminNavigator.tsx`
- Create: `frontend/src/features/admin/components/GraphInspector.tsx`, `RelationshipEditor.tsx`
- Create: `frontend/src/features/admin/components/MutationPreview.tsx`, `GraphHealthPanel.tsx`
- Create: `frontend/src/features/admin/components/ArchivePersonDialog.tsx`, `AuditLog.tsx`
- Create: `frontend/src/features/admin/components/IntegrationStatus.tsx`
- Create: unit and component tests beside these modules.
- Modify: `frontend/src/app/admin/page.tsx`
- Delete: `frontend/src/components/AdminTreeEditor.tsx`

**Interfaces:**
- Consumes: admin snapshot, mutation preview, and commit endpoints.
- Produces: a responsive three-pane workspace and deliberate preview-before-commit editing.

Use this state contract:

```ts
export type AdminMode = "graph" | "submissions" | "quality" | "history";

export interface AdminWorkspaceState {
  mode: AdminMode;
  selectedPersonId: PersonId | null;
  selectedFamilyUnitId: FamilyUnitId | null;
  query: string;
  issueSeverity: "all" | "error" | "warning";
  draft: MutationDraft | null;
  preview: MutationPreview | null;
  snapshotRevision: number;
}
```

- [ ] **Step 1: Write workspace reducer and responsive-layout tests**

Assert mode changes preserve selection/query, selecting a person clears a selected
family unit, beginning a second draft is blocked until the first is discarded,
stale snapshot actions clear preview, and issue selection switches to Graph mode
and focuses the affected stable ID.

Render at 1440px and assert navigator, canvas, and inspector landmarks are present.
At 768px the navigator is a dismissible drawer. At 390px render a searchable list
and full-screen inspector, omit drag handles, and retain every graph action as a
labelled command.

- [ ] **Step 2: Implement the workspace shell and reducer**

Use this desktop grid and collapse it with container queries:

```css
.admin-workspace {
  container-type: inline-size;
  display: grid;
  grid-template-columns: minmax(240px, 300px) minmax(0, 1fr) minmax(320px, 400px);
  grid-template-rows: 48px minmax(0, 1fr);
  height: calc(100dvh - var(--app-header-height));
  min-height: 620px;
}
```

Below 1024px hide the navigator track behind a drawer. Below 768px set one
`minmax(0, 1fr)` column, `min-height: 0`, and render either the list/canvas or the
full-screen inspector; never retain the three desktop tracks off-screen.

`AdminToolbar` contains the Graph/Submissions/Quality/History segmented control,
revision, health status, search, add, fit, refresh, and account menu. Use icon
buttons with tooltips for compact commands. No pane is wrapped in a decorative
card, and pane resizing cannot overlap the toolbar.

- [ ] **Step 3: Write relationship-draft tests**

Test create union, single-parent unit, parent-child link, remarriage, change primary
placement, archive person, unresolved proposal, cycle preview rejection, stale
revision, and idempotency-key stability. Drafts always include snapshot revision.

- [ ] **Step 4: Implement draft builders and focused inspector actions**

`relationship-draft.ts` exports pure builders returning backend command DTOs. Each
builder requires branded IDs and has no name-based matching. Generate one UUID
idempotency key when a draft begins and preserve it through preview and commit.

`GraphInspector` offers exactly Add parent, Add child, Create union, Add another
union, Change primary placement, Edit person, and Archive for a selected person.
Family units expose Edit union, Add child, End union, and Supersede. Use stable-ID
comboboxes with name, dates, and branch context; unresolved text is a separate
field. Keep unsaved drafts in memory and warn before route unload.

- [ ] **Step 5: Make drag/drop draft-only**

A person-to-person drop opens a relation-choice menu containing Create union,
Make parent of, and Make child of; no option is preselected. A person-to-family
drop offers Add adult or Add child only when structurally possible. The chosen
option calls the same pure builder and dispatches `BEGIN_DRAFT`. The drop handler
may not import `admin.ts`, call `fetch`, call `previewMutation`, or call
`commitMutation`. Component tests spy on all API functions and assert zero calls
after the drop itself. Invalid targets expose a disabled cursor and the precise
local reason.

- [ ] **Step 6: Require preview and explicit confirmation**

`MutationPreview` lists commands, affected people/branches, new revision,
checksum, semantic before/after values, errors, and warnings. Disable confirm on
errors or expiry. Commit sends exact preview revision, digest, expiry, and
idempotency key. On `409`, discard
the preview, refresh the snapshot, retain the selected stable ID, and require a new
preview; never retry silently.

- [ ] **Step 7: Implement quality, audit, archive, and integration modes**

`GraphHealthPanel` displays structured issues. `ArchivePersonDialog` lists
dependent links and cannot confirm until a valid reassignment/removal preview
exists. `AuditLog` offers compensation through preview. `IntegrationStatus` shows
configured/enabled status only. Selecting an issue focuses affected records.
Remove Heal Graph, direct delete, editable secret fields, free-text ID
contradictions, raw JSON, and process-local undo.

- [ ] **Step 8: Replace authentication UI**

`AdminLogin` posts to `/api/auth/login`; `AdminWorkspace` checks `/api/auth/session`.
Logout posts to its route. Remove all token props, Authorization headers, and
`localStorage` access from `admin/page.tsx` and descendants.

- [ ] **Step 9: Run and commit**

```powershell
npm test -- src/features/admin src/app/admin src/app/api/auth src/app/api/admin
npm run typecheck
npm run lint
git rm frontend/src/components/AdminTreeEditor.tsx
git add frontend/src/features/admin frontend/src/app/admin
git commit -m "feat: add safe Shajra admin graph editing"
```

### Task 9: Field-Level AI Submission Review

**Files:**
- Create: `frontend/src/features/enrichment/types.ts`, `api.ts`, `review-state.ts`
- Create: `frontend/src/features/enrichment/components/SubmissionQueue.tsx`
- Create: `frontend/src/features/enrichment/components/SubmissionReviewWorkspace.tsx`
- Create: `frontend/src/features/enrichment/components/SubmissionComparison.tsx`
- Create: `frontend/src/features/enrichment/components/SuggestionDecisionRow.tsx`
- Create: `frontend/src/features/enrichment/components/AttemptTimeline.tsx`
- Create: tests beside every enrichment module.
- Modify: `frontend/src/features/admin/components/AdminWorkspace.tsx`

**Interfaces:**
- Consumes: authenticated submission/enrichment endpoints and manual mutation draft builders.
- Produces: complete field-level decisions and `ReviewDraftResult`; it never commits directly.

Use these API signatures:

```ts
export type ReviewQueueStatus = "new" | "enriched" | "needs_review" | "failed" | "ready_to_apply" | "resolved";
export type ReviewDecisionType = "accept" | "reject" | "replace" | "unresolved";

export interface ReviewDecision {
  suggestionKey: string;
  decision: ReviewDecisionType;
  replacementPersonId?: PersonId;
  replacementValue?: string;
}

export interface CompletedReview {
  attemptId: AttemptId;
  graphRevision: number;
  decisions: ReviewDecision[];
}

export interface ReviewDraftResult {
  reviewId: ReviewId;
  mutationDraft: MutationDraft | null;
  status: "ready_to_apply" | "resolved";
}

export function fetchSubmissionQueue(status: ReviewQueueStatus): Promise<SubmissionSummary[]>;
export function fetchSubmissionReview(id: SubmissionId): Promise<SubmissionReviewDetail>;
export function enrichSubmission(id: SubmissionId, idempotencyKey: string): Promise<EnrichmentAttempt>;
export function fetchEnrichmentAttempts(id: SubmissionId): Promise<EnrichmentAttempt[]>;
export function saveReview(id: SubmissionId, review: CompletedReview): Promise<ReviewDraftResult>;
```

- [ ] **Step 1: Write strict DTO and review-state tests**

Reject unknown statuses, candidate IDs outside the attempt candidate set,
confidence outside `[0, 1]`, provider exception text, extra keys, and reviews that
omit a suggestion decision. Test filters for new, enriched, needs-review, failed,
ready-to-apply, and resolved. A review is complete only when every suggestion is accept, reject,
replace, or unresolved. `replace` requires exactly one replacement matching the
suggestion kind; all other decisions reject replacement fields.

- [ ] **Step 2: Implement typed clients and review reducer**

Parse every response with Zod. The reducer stores decisions by immutable
`suggestionKey`, keeps raw/normalized data read-only, and generates one
idempotency key for each enrich or review action. `saveReview` returns a review ID
and optional draft wrapper and has no access to `commitMutation`. A no-command
review returns `resolved`; a review with commands returns `ready_to_apply`.

- [ ] **Step 3: Build the queue and comparison workspace**

`SubmissionQueue` is a dense, searchable list with status, submitted age,
duplicate warning, attempt count, and sanitized failure badge. Desktop
`SubmissionComparison` uses three columns named Raw, Normalized, and Suggestions;
tablet/mobile use tabs. It renders typed fields, never a JSON `<pre>`.

- [ ] **Step 4: Build field-level decisions**

Each `SuggestionDecisionRow` shows current canonical value, proposed value,
confidence, evidence, reason, alternatives, and Accept/Reject/Replace/Unresolved
controls. Replace uses a stable-ID combobox for relations. Keyboard focus advances
to the next undecided row after a decision but remains reversible before save.

- [ ] **Step 5: Implement enrichment attempts and failure recovery**

`AttemptTimeline` shows prompt version, model label, running/succeeded/failed/
abandoned status, timings, and sanitized code. Enrich and Retry are explicit
buttons. A timeout or provider error keeps manual review enabled and preserves raw
values. Disable duplicate enrichment while an attempt is running.

- [ ] **Step 6: Feed reviewed suggestions into normal preview**

After `saveReview`, dispatch `result.mutationDraft` into `AdminWorkspace`, switch
to Graph mode, focus affected records, and open `MutationPreview`. Preserve
`result.reviewId` on the draft. Assert
`commitMutation` has zero calls until the administrator confirms that preview.

- [ ] **Step 7: Run and commit**

```powershell
npm test -- src/features/enrichment src/features/admin
npm run typecheck
npm run lint
git add frontend/src/features/enrichment frontend/src/features/admin
git commit -m "feat: add reviewable Shajra AI suggestions"
```

### Task 10: Normalize Remaining Routes and Accessibility

**Files:**
- Modify: `frontend/src/app/page.tsx`, `frontend/src/app/member/[id]/page.tsx`
- Modify: `frontend/src/app/search/page.tsx`, `frontend/src/app/map/page.tsx`
- Create: `frontend/src/app/people/page.tsx`, `frontend/src/app/stories/page.tsx`
- Create: `frontend/src/features/people/PeopleDirectory.tsx`, `PersonSummary.tsx`
- Create: `frontend/src/features/profile/PersonProfile.tsx`, `FamilyUnitsList.tsx`
- Create: `frontend/src/features/content/StoriesList.tsx`, `AlbumsList.tsx`
- Modify: `frontend/src/components/Navbar.tsx`, `GlobeMap.tsx`
- Modify: `frontend/src/app/layout.tsx`, `globals.css`
- Create: route component tests.

**Interfaces:**
- Consumes: public v2 API and stable `PersonId` routes.
- Produces: consistent public experience and accessibility baseline.

- [ ] **Step 1: Convert member, search, map, and home routes**

Use v2 DTOs and application IDs. Remove parent/spouse lookup by Airtable ID.
Member pages list every normalized family unit and non-primary reference. Search
and map show explicit failures rather than empty arrays. Escape all map tooltip
text through DOM text nodes, not HTML strings. Wrap the rendered globe and its
stable aspect-ratio container in `data-testid="heritage-globe"` for pixel checks.
Use `aspect-ratio: 16 / 9` with `max-height: 70dvh` on desktop and
`aspect-ratio: 4 / 5` on mobile so controls cannot resize the canvas.

Home starts with the Shajra name, family search, and actual people/recent heritage
content in the first viewport; it has no marketing feature grid or decorative
hero. People is a searchable, filterable directory with list/grid segmented mode.
Stories and albums use real family media and explicit empty/error states. Every
person link uses `/member/{personId}` and can continue to Tree with the same ID.

- [ ] **Step 2: Add navigation and global accessibility**

Add a skip link, visible focus, reduced-motion media query, semantic landmarks,
and keyboard-operable mobile navigation. Ensure color is not the sole indicator
of gender, status, issue severity, or selected graph node.

Keep route-level loading, empty, partial, and error copy consistent through
`AsyncState`. Preserve query/filter URL state on People, Search, Map, and Stories
where applicable, and restore focus to the page heading after navigation.

- [ ] **Step 3: Enforce stable dimensions and responsive text**

Use fixed graph/control dimensions, `min-width: 0`, wrapping labels, and no
viewport-width font scaling. Keep cards at `border-radius: 8px` or less. Confirm
buttons and labels do not overflow at 320px width.

- [ ] **Step 4: Run the frontend gate**

```powershell
npm run lint
npm run typecheck
npm test
npm run build
```

Expected: zero errors, warnings reviewed, and all routes generated.

- [ ] **Step 5: Commit route migration**

```powershell
git add frontend/src
git commit -m "feat: migrate Shajra public routes to stable IDs"
```

### Task 11: Geometry, Accessibility, and No-Write Browser Tests

**Files:**
- Create: `frontend/e2e/tree-geometry.spec.ts`
- Create: `frontend/e2e/tree-accessibility.spec.ts`
- Create: `frontend/e2e/submission.spec.ts`
- Create: `frontend/e2e/admin-preview.spec.ts`
- Create: `frontend/e2e/admin-workspace.spec.ts`, `admin-enrichment.spec.ts`
- Create: `frontend/e2e/public-routes.spec.ts`

**Interfaces:**
- Consumes: fixture-backed app.
- Produces: objective visual and interaction acceptance evidence.

- [ ] **Step 1: Add a global no-write network guard**

Abort and fail any unexpected mutating request. Allow only mocked same-origin
submission and admin-preview routes. Assert no request URL contains `railway.app`.

- [ ] **Step 2: Implement geometry assertions**

At 390x844, 768x1024, and 1440x900, inspect `[data-id]` node rectangles and graph
controls. Assert zero pairwise node intersections, zero control intersections,
and each SVG connector endpoint lies within two CSS pixels of its named handle.
Run initial fit and 100 percent zoom.

- [ ] **Step 3: Assert deterministic layout and interactions**

Reload the same fixture ten times, collect the rounded layout hash exposed through
a test-only data attribute, and assert one value. Exercise fit tree, select person,
fit branch, zoom, pan, list mode, and back to canvas; selection and toolbar bounds
must remain stable.

- [ ] **Step 4: Add Axe and keyboard tests**

Run Axe on tree canvas, list mode, submission, login, admin editor, member, search,
people, stories, and map routes. Fail on serious or critical violations. Traverse every graph
control and list person with keyboard only, verify visible focus, and assert the
list fallback accounts for all fixture people.

- [ ] **Step 5: Add form and admin no-write flows**

Fill a valid submission, simulate accepted raw record and failed media retry, and
verify values persist. In admin, preview a remarriage, reject a cycle, simulate
`409`, refresh, and re-preview. Drop a person onto a family unit and assert zero
mutating requests until preview confirmation. Simulate failed AI enrichment,
complete manual field decisions, accept one suggestion into a draft, and assert
the graph remains unchanged until the standard preview is confirmed. All backend
writes are intercepted fixtures.

- [ ] **Step 6: Verify responsive admin and public UX visually**

At 390x844, 768x1024, and 1440x900 capture fixture screenshots for Graph,
Submissions, Quality, History, relationship preview, failed AI attempt, public
Tree, member profile, People, Map, and submission Review. Assert toolbar, panes,
dialogs, bottom sheets, text, and controls have zero bounding-box intersections.
Desktop exposes three admin panes; tablet uses a navigator drawer; mobile exposes
list plus inspector and no drag handle.

For the existing Three.js globe, decode its screenshot with `pngjs`, mask the page
background, use `[data-testid="heritage-globe"]` as the expected box, and require
at least 5,000 non-background pixels spanning 60 percent of that box. Drag to
rotate, capture again, and require a changed
pixel hash. Assert the canvas is nonblank, fully framed, and does not overlap map
filters at mobile or desktop sizes.

- [ ] **Step 7: Run and commit**

```powershell
npm run test:e2e
npm run lint
npm run typecheck
npm test
npm run build
git add frontend/e2e frontend/playwright.config.ts
git commit -m "test: verify Shajra graph geometry and workflows"
```

## Completion Gate

This plan is complete only when every frontend gate passes, the fixed viewport
geometry matrix has zero unintended overlaps, ten layout runs hash identically,
Axe reports zero serious or critical violations, the admin pane/draft/AI-review
flows pass at all three viewports, public routes share stable person identity and
explicit states, the globe pixel/rotation checks pass on mobile and desktop, no
browser test sends a real write, and the worktree is clean. Continue with
`2026-08-03-shajra-rollout-and-verification.md`.
