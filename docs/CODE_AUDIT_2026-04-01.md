# Code Audit Report (April 1, 2026)

## Scope
This audit reviewed authentication, authorization, token handling, API key lifecycle, and runtime test posture for the backend API.

Reviewed modules:
- `auth/jwt.py`
- `auth/middleware.py`
- `api/v1/auth.py`
- `api/v1/api_keys.py`
- `api/deps.py`
- `core/config.py`

## Executive Summary
- **Overall posture:** moderate.
- **Critical:** 0
- **High:** 2 (one fixed in this branch)
- **Medium:** 2
- **Low:** 1

## Findings

### 1) Missing authorization guard for API key management endpoints (**High, fixed**)
**What was found**
- API key create/list/revoke endpoints were protected by authentication only (tenant context from JWT), but did not enforce admin-level authorization.

**Risk**
- Any authenticated user in a tenant could create and revoke org-wide API keys, enabling privilege escalation and credential misuse.

**Evidence**
- Router previously lacked dependencies for `require_scope("agenticorg:admin")` in `api/v1/api_keys.py`.
- Scope checker behavior in `api/deps.py` allows admin bypass via `agenticorg:admin*` prefixes.

**Remediation**
- Added router-level dependency requiring admin scope for all API key endpoints.

---

### 2) Stateful auth controls rely on in-memory process state (**High, open**)
**What was found**
- Login/signup/reset rate limits and token blacklist keep state in module-level dictionaries.
- Redis is used as best-effort for blacklist but memory remains the primary write path.

**Risk**
- In multi-replica deployments, enforcement becomes inconsistent (bypass by node hopping).
- Service restarts clear in-memory state and unblock previously throttled/revoked actors until Redis is checked/warmed.

**Evidence**
- In-memory stores in `auth/middleware.py` (`_failed_attempts`, `_blocked_ips`) and `api/v1/auth.py` (`_signup_attempts`, `_login_attempts`, `_reset_attempts`).
- Token blacklist in-memory first pattern in `auth/jwt.py`.

**Recommended remediation**
- Move all rate-limit counters and blocks to Redis with atomic TTL keys.
- Make Redis authoritative for revocation checks, with optional small local cache for hot paths.

---

### 3) Email canonicalization is inconsistent across auth flows (**Medium, open**)
**What was found**
- `forgot-password` normalizes email to lowercase/trim, while signup/login rely on raw request email.

**Risk**
- Duplicate users and login confusion due to case-sensitive behavior differences.
- Operational support burden and account integrity ambiguity.

**Evidence**
- Lowercasing in `api/v1/auth.py` (`forgot_password`).
- Non-normalized comparisons in signup/login queries.

**Recommended remediation**
- Normalize email (`strip().lower()`) consistently at API boundary for signup/login/google linking/reset.
- Add DB-level unique index on canonicalized email per tenant/global policy.

---

### 4) Python runtime/tooling drift in CI/dev environment (**Medium, open**)
**What was found**
- Project declares Python >=3.11, but local test environment ran 3.10.
- Pytest defaults assume plugins (`pytest-cov`, `pytest-asyncio`) not always installed.

**Risk**
- False-negative test runs and unpredictable developer feedback loops.

**Evidence**
- `pyproject.toml` requires `>=3.11`.
- Collection/import warnings/errors observed under Python 3.10 for `datetime.UTC` and missing plugin options.

**Recommended remediation**
- Enforce runtime via `.python-version` / CI matrix hard check.
- Ensure dev bootstrap installs optional `dev` dependencies before test execution.

---

### 5) Password policy baseline could be stronger (**Low, open**)
**What was found**
- Password policy enforces length >=8 and character classes.

**Risk**
- Meets minimum standards but can be improved for stronger entropy and modern policy alignment.

**Recommended remediation**
- Increase minimum length to 12.
- Add compromised password screening (HIBP k-anonymity) for signup/reset.

## Validation Performed
- Focused static review of authn/authz and API key code paths.
- Targeted unit test execution (`tests/unit/test_auth.py`) with local `pytest` overrides due to environment plugin mismatch.

## Follow-up Backlog
1. Redis-authoritative rate limiter + revocation service.
2. Canonical email handling + migration.
3. CI runtime conformance checks.
4. Expanded security regression tests around API key authorization boundaries.
