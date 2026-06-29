# JanusGate Phase 1 Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the JanusGate Phase 1 foundation from scratch with FastAPI, secure defaults, Identity/Auth, PolicyDecisionService, Connector API v2, and Credential Vault interfaces.

**Architecture:** JanusGate starts as a modular monolith with strict bounded contexts and future service boundaries. The first code owner establishes secure infrastructure and Identity/Auth; the second code owner builds policy, connector, and vault layers on top of that foundation.

**Tech Stack:** Python 3.12/3.13+, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, PostgreSQL, Redis, React 19 + TypeScript + Vite + Ant Design, Docker Compose, OpenTelemetry, pytest, ruff, mypy, bandit, pip-audit.

---

## Coordination Rules

- Shared repository: `git@github.com:Lynn-Lee/JanusGate.git`
- Shared branch: `dev`
- Before every task:

```bash
git fetch origin
git pull --ff-only origin dev
git status --short --branch
```

Expected: local branch is up to date with `origin/dev` and has no unrelated local changes.

- Do not copy JumpServer implementation code. Use JumpServer only for business requirements, user paths, and migration references.
- Do not modify files owned by the other agent unless the change is explicitly coordinated in #jumpserver.

---

## Task 1: Project Foundation and Security Baseline

**Owner:** deepseek-architect

**Files:**
- Create: `pyproject.toml`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `src/janusgate/main.py`
- Create: `src/janusgate/core/config.py`
- Create: `src/janusgate/core/security.py`
- Create: `src/janusgate/core/crypto.py`
- Create: `src/janusgate/core/rate_limit.py`
- Create: `tests/core/test_security.py`
- Create: `tests/core/test_crypto.py`

- [ ] **Step 1: Pull the shared baseline**

```bash
git fetch origin
git pull --ff-only origin dev
git status --short --branch
```

Expected: clean branch tracking `origin/dev`.

- [ ] **Step 2: Add FastAPI foundation with health endpoint**

Implement `src/janusgate/main.py` with an app factory and `/healthz` endpoint returning `{"status":"ok"}`.

- [ ] **Step 3: Add secure configuration module**

Implement `src/janusgate/core/config.py` with fail-closed validation for secret keys, database URL, Redis URL, CORS origins, secure cookie flags, and environment mode.

- [ ] **Step 4: Add crypto primitives**

Implement `src/janusgate/core/crypto.py` with AES-256-GCM encrypt/decrypt helpers that require a 32-byte key and generate a unique nonce for every encryption.

- [ ] **Step 5: Add password hashing and token helpers**

Implement `src/janusgate/core/security.py` with Argon2id or bcrypt password hashing, constant-time compare, JWT creation, JWT verification, and token revocation hook interface.

- [ ] **Step 6: Add rate-limit foundation**

Implement `src/janusgate/core/rate_limit.py` with Redis-backed fixed-window or sliding-window limit checks for login, token, and connector registration paths.

- [ ] **Step 7: Add tests for fail-closed and crypto behavior**

Run:

```bash
pytest tests/core -v
```

Expected: config rejects unsafe defaults; AES-GCM round-trip passes; tampered ciphertext fails; password verification passes; invalid JWT fails.

- [ ] **Step 8: Commit and push**

```bash
git add pyproject.toml Dockerfile docker-compose.yml src/janusgate tests/core
git commit -m "feat: initialize FastAPI security foundation"
git push origin dev
```

---

## Task 2: Identity and Auth Module

**Owner:** deepseek-architect

**Files:**
- Create: `src/janusgate/identity/models.py`
- Create: `src/janusgate/identity/schemas.py`
- Create: `src/janusgate/identity/service.py`
- Create: `src/janusgate/identity/routes.py`
- Create: `tests/identity/test_login.py`
- Create: `tests/identity/test_api_keys.py`

- [ ] **Step 1: Pull latest foundation**

```bash
git fetch origin
git pull --ff-only origin dev
```

Expected: includes Task 1 commit.

- [ ] **Step 2: Add user and credential models**

Create SQLAlchemy models for users, password credentials, API keys, MFA factors, and revoked tokens.

- [ ] **Step 3: Add login service**

Implement login with rate limiting, password verification, short-lived access token, refresh token rotation, and audit event hook.

- [ ] **Step 4: Add API key service**

Implement API key creation with one-time secret display, hashed storage, scoped permissions, expiry, revocation, and last-used timestamp.

- [ ] **Step 5: Add tests**

Run:

```bash
pytest tests/identity -v
```

Expected: successful login returns token; wrong password increments limit; revoked token fails; API key secret is not stored in plaintext.

- [ ] **Step 6: Commit and push**

```bash
git add src/janusgate/identity tests/identity
git commit -m "feat: add identity and auth foundation"
git push origin dev
```

---

## Task 3: PolicyDecisionService Core

**Owner:** tc-codex-architect

**Files:**
- Create: `docs/architecture/02-policy-decision-service.md`
- Create: `src/janusgate/policy/models.py`
- Create: `src/janusgate/policy/schemas.py`
- Create: `src/janusgate/policy/decision.py`
- Create: `src/janusgate/policy/routes.py`
- Create: `tests/policy/test_decision.py`

- [ ] **Step 1: Pull Identity/Auth baseline**

```bash
git fetch origin
git pull --ff-only origin dev
```

Expected: includes Task 1 and Task 2 commits.

- [ ] **Step 2: Document policy decision contract**

Write `docs/architecture/02-policy-decision-service.md` defining request fields: subject, action, resource, context, risk signals, approval state, MFA state, and connector identity. Define response fields: decision, reason code, explain trace, obligations, TTL, audit event id.

- [ ] **Step 3: Add deny-by-default decision engine**

Implement `PolicyDecisionService.evaluate()` so unknown subject, unknown resource, missing action, expired approval, failed MFA, or missing connector trust all return deny with explicit reason code.

- [ ] **Step 4: Add allow path for explicit policy rule**

Implement an allow decision only when subject, action, resource, time window, MFA requirement, and approval requirement are all satisfied.

- [ ] **Step 5: Add tests**

Run:

```bash
pytest tests/policy -v
```

Expected: default deny passes; explicit allow passes; expired approval denies; MFA-required-without-MFA denies; explain trace is present for every decision.

- [ ] **Step 6: Commit and push**

```bash
git add docs/architecture/02-policy-decision-service.md src/janusgate/policy tests/policy
git commit -m "feat: add policy decision service core"
git push origin dev
```

---

## Task 4: Connector API v2 Protocol

**Owner:** tc-codex-architect

**Files:**
- Create: `docs/architecture/03-connector-api-v2.md`
- Create: `src/janusgate/connectors/models.py`
- Create: `src/janusgate/connectors/schemas.py`
- Create: `src/janusgate/connectors/registry.py`
- Create: `src/janusgate/connectors/routes.py`
- Create: `tests/connectors/test_registration.py`

- [ ] **Step 1: Pull policy baseline**

```bash
git fetch origin
git pull --ff-only origin dev
```

Expected: includes Task 3 commit.

- [ ] **Step 2: Document Connector API v2**

Write protocol for registration, key rotation, heartbeat, capability reporting, short-lived connection token request, audit event delivery, and connector deactivation.

- [ ] **Step 3: Implement connector registry**

Implement connector identity, public key fingerprint, allowed capabilities, environment, status, last heartbeat, and token issuance constraints.

- [ ] **Step 4: Integrate policy check before token issuance**

Connector token issuance must call `PolicyDecisionService.evaluate()` and deny on any non-allow decision.

- [ ] **Step 5: Add tests**

Run:

```bash
pytest tests/connectors -v
```

Expected: unsigned registration fails; inactive connector cannot request tokens; denied policy blocks token; allowed policy returns short-lived token with TTL.

- [ ] **Step 6: Commit and push**

```bash
git add docs/architecture/03-connector-api-v2.md src/janusgate/connectors tests/connectors
git commit -m "feat: add connector api v2 foundation"
git push origin dev
```

---

## Task 5: Credential Vault Interface

**Owner:** tc-codex-architect

**Files:**
- Create: `docs/architecture/04-credential-vault.md`
- Create: `src/janusgate/vault/provider.py`
- Create: `src/janusgate/vault/models.py`
- Create: `src/janusgate/vault/schemas.py`
- Create: `src/janusgate/vault/service.py`
- Create: `tests/vault/test_provider.py`

- [ ] **Step 1: Pull connector baseline**

```bash
git fetch origin
git pull --ff-only origin dev
```

Expected: includes Task 4 commit.

- [ ] **Step 2: Document SecretProvider interface**

Define create, read, rotate, revoke, lease, unwrap, and audit methods. Define provider types: local encrypted store, HashiCorp Vault, cloud KMS-backed store.

- [ ] **Step 3: Implement provider abstraction**

Implement a provider interface that never returns plaintext except through an explicit lease/unwrap flow with policy-approved context.

- [ ] **Step 4: Add local encrypted provider for development**

Use AES-GCM from `src/janusgate/core/crypto.py`; store nonce and ciphertext separately; record key version.

- [ ] **Step 5: Add tests**

Run:

```bash
pytest tests/vault -v
```

Expected: plaintext is never persisted; tampered ciphertext fails; revoked credential cannot be unwrapped; rotation creates a new version.

- [ ] **Step 6: Commit and push**

```bash
git add docs/architecture/04-credential-vault.md src/janusgate/vault tests/vault
git commit -m "feat: add credential vault interface"
git push origin dev
```

---

## Task 6: Phase 1 Integration Gate

**Owner:** tc-codex-architect + deepseek-architect

**Files:**
- Create: `docs/architecture/05-phase-1-integration-review.md`
- Modify: `.github/workflows/*`
- Modify: `README.md`

- [ ] **Step 1: Pull all Phase 1 work**

```bash
git fetch origin
git pull --ff-only origin dev
```

Expected: includes Tasks 1-5 commits.

- [ ] **Step 2: Run full validation**

```bash
pytest -v
ruff check .
mypy src
bandit -r src
pip-audit
```

Expected: all checks pass or have documented explicit exceptions approved in `docs/architecture/05-phase-1-integration-review.md`.

- [ ] **Step 3: Write Phase 1 review**

Document implemented modules, remaining risks, security exceptions, migration assumptions, and Phase 2 entry criteria.

- [ ] **Step 4: Commit and push**

```bash
git add docs/architecture/05-phase-1-integration-review.md README.md .github/workflows
git commit -m "docs: add phase 1 integration review"
git push origin dev
```
