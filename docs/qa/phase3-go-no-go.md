# Phase 3 QA Go/No-Go Evidence Package

Decision: GO

Date: 2026-07-02
Scope: Phase 3 MVP acceptance gate from `docs/architecture/10-master-evaluation-and-roadmap.md`.

## Summary

Phase 3 is ready to close for the current MVP scope. The repository now has evidence for the 6-page console surface, the API-level JIT-to-session main chain, security fail-closed cases, backend coverage gate, frontend quality gates, Compose `/health` smoke, Helm render smoke, and updated API/deploy documentation.

The GO decision is scoped to the Phase 3 MVP. Phase 4 must still replace in-memory MVP seams with production-grade multi-tenant enforcement, credential rotation, SSH CA, connector trust, and shared Vault/session/token stores.

## Product acceptance

| Requirement | Evidence | Result |
| --- | --- | --- |
| Login page | `frontend/src/App.test.tsx`, `frontend/src/pages/mvp-pages.test.tsx` | Pass |
| Assets page | `frontend/src/pages/mvp-pages.test.tsx`, `backend/tests/test_asset_api_and_service.py` | Pass |
| Sessions page | `frontend/src/pages/mvp-pages.test.tsx`, `backend/tests/test_sessions.py` | Pass |
| Workflow/JIT page | `frontend/src/pages/mvp-pages.test.tsx`, `backend/tests/workflows/test_workflow_service_and_api.py` | Pass |
| Audit Logs page | `frontend/src/pages/mvp-pages.test.tsx`, `backend/tests/audits/test_audit_api.py` | Pass |
| Settings page | `frontend/src/pages/mvp-pages.test.tsx` | Pass |
| Main chain smoke | `backend/tests/test_phase3_api_smoke.py` covers request, submit, approve, connection token, session create, revoke, closed session list, and audit lookup | Pass |

## Security acceptance

| Requirement | Evidence | Result |
| --- | --- | --- |
| Protected pages require auth | `frontend/src/App.test.tsx`; backend protected routes use `current_user` / permission dependencies | Pass |
| User cannot self-approve | `backend/tests/test_api_contracts.py`, `backend/tests/workflows/test_workflow_service_and_api.py` | Pass |
| User data is scoped by actor and tenant | `backend/tests/test_sessions.py`, `backend/tests/workflows/test_workflow_service_and_api.py`, `backend/tests/audits/test_audit_api.py` | Pass |
| Grant mismatch, revoked, expired, or resource mismatch fails closed | `backend/tests/test_sessions.py`, `backend/tests/workflows/test_workflow_service_and_api.py` | Pass |
| Revoke closes active bound sessions | `backend/tests/test_phase3_api_smoke.py`, `backend/tests/workflows/test_workflow_audit.py` | Pass |
| Audit metadata does not leak token, password, connection string, or secret | `backend/tests/test_phase3_api_smoke.py`, `backend/tests/audits/test_audit_api.py`, `frontend/src/pages/auditRedaction.test.ts` | Pass |

## Backend quality

Backend lint, typecheck, tests, coverage, security scan, and dependency scan are mandatory for the Phase 3 gate.

## Frontend quality

Frontend lint, typecheck, tests, and production build are mandatory for the Phase 3 gate.

## Deployability

Compose config, Compose `/health` smoke, Helm lint, and Helm render are mandatory for the Phase 3 gate.

## Documentation

The API contract, deployment guide, master roadmap, README, and this QA evidence package are mandatory for the Phase 3 gate.

## Quality gate commands

| Gate | Required command | Result |
| --- | --- | --- |
| Backend lint | `cd backend && ruff check .` | Required before merge |
| Backend types | `cd backend && mypy app` | Required before merge |
| Backend tests and coverage | `cd backend && pytest -q --cov=app --cov-report=term-missing --cov-fail-under=80` | Required before merge and CI |
| Backend security scan | `cd backend && bandit -q -r app` | Required before merge |
| Backend dependency scan | `cd backend && pip-audit --skip-editable` | Required before merge |
| Frontend lint | `cd frontend && npm run lint` | Required before merge |
| Frontend types | `cd frontend && npm run typecheck` | Required before merge |
| Frontend tests | `cd frontend && npm test -- --run` | Required before merge |
| Frontend build | `cd frontend && npm run build` | Required before merge |
| Compose config | `docker compose config` with disposable CI `.env` | Required before merge and CI |
| Compose health smoke | `scripts/phase3-compose-health-smoke.sh` | Required before merge and CI |
| Helm lint/render | `helm lint deploy/helm/janusgate`; `helm template janusgate deploy/helm/janusgate` with disposable CI `secret.secretKey` / `secret.databaseUrl` values | Required in CI; local run depends on Helm availability |

## Deployability evidence

CI executes the Phase 3 deploy smoke through `.github/workflows/ci.yml`: `docker compose config`, `scripts/phase3-compose-health-smoke.sh`, `helm lint deploy/helm/janusgate`, and `helm template janusgate deploy/helm/janusgate` with disposable test values for the required Helm secret fields.

The Compose smoke starts backend plus dependencies, calls `/health`, and removes its containers and volumes on exit. Helm remains single-replica by default until Phase 4 introduces a shared connection-token/session store.

## Documentation evidence

| Document | Status |
| --- | --- |
| `docs/architecture/10-master-evaluation-and-roadmap.md` | Tracks Phase 3 #t41 and Phase 4 startup condition |
| `docs/api-contract.md` | Documents Phase 3 API and connection token contract |
| `deploy/README.md` | Documents CI, Compose, Helm, and shared token-store deployment boundary |
| `README.md` | Lists Phase 3 smoke and QA evidence entry points |

## Next scope

Start Phase 4 with `#t42` multi-tenant and organization/team/project permission modeling. Keep it as a separate architecture/backend slice before credentials, SSH CA, connector trust, or Vault production backends.
