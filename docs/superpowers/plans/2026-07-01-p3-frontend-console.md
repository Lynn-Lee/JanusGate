# Phase 3 Frontend Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the JanusGate Phase 3 MVP frontend console with 6 pages and one Workflow/JIT main-chain surface.

**Architecture:** Add a standalone `frontend/` Vite + React + TypeScript + Ant Design app. Keep API access behind a typed client that supports the Phase 3 ErrorResponse contract and tolerates current FastAPI `detail` responses. Use client-side routing with protected console layout and local session cache only where the backend does not yet expose a session list endpoint.

**Tech Stack:** React 19, TypeScript, Vite, Ant Design, React Router, Vitest + Testing Library.

---

### Task 1: Frontend project baseline

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/index.html`
- Create: `frontend/src/test/setup.ts`
- Test: `frontend/src/api/client.test.ts`

- [ ] Create a frontend package with `test`, `typecheck`, `lint`, and `build` scripts.
- [ ] Write an API-client failing test for stable error parsing.
- [ ] Verify the test fails because `parseApiError` is missing.
- [ ] Implement typed API client and rerun the test.

### Task 2: Shell, auth, and routing

**Files:**
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/auth/AuthContext.tsx`
- Create: `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/pages/LoginPage.tsx`
- Test: `frontend/src/App.test.tsx`

- [ ] Write failing tests for protected redirect and five console nav entries.
- [ ] Implement auth provider, login page, protected layout, and navigation.
- [ ] Rerun focused tests.

### Task 3: MVP pages and main chain affordances

**Files:**
- Create: `frontend/src/pages/AssetsPage.tsx`
- Create: `frontend/src/pages/SessionsPage.tsx`
- Create: `frontend/src/pages/WorkflowPage.tsx`
- Create: `frontend/src/pages/AuditsPage.tsx`
- Create: `frontend/src/pages/SettingsPage.tsx`
- Create: `frontend/src/components/StatusView.tsx`
- Test: `frontend/src/pages/mvp-pages.test.tsx`

- [ ] Write failing tests for Assets JIT CTA, Workflow request/grant panels, Audit metadata redaction, and Settings config summary.
- [ ] Implement pages using typed API client and safe empty/error/loading states.
- [ ] Store created/closed session responses in local console cache until backend exposes a list API.
- [ ] Rerun focused tests.

### Task 4: Verification and docs

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture/08-phase3-mvp-prd-ia.md`

- [ ] Document frontend local commands and current session-list boundary.
- [ ] Run `npm --prefix frontend run test`, `typecheck`, `lint`, `build`, and `git diff --check`.
- [ ] Add task checkpoint with verification summary.
