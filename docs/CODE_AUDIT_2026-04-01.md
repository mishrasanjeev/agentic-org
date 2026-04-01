# Code Audit — April 1, 2026

## Scope
- Reviewed authentication middleware and JWT validation paths for abuse resistance and tenant-isolation behavior.
- Executed targeted auth/security tests that do not require async pytest plugins.
- Performed focused static review for high-impact security and reliability concerns.

## Findings

### 1) Failure counter was not reset after successful authentication (fixed)
**Severity:** Medium

`AuthMiddleware` tracked failed attempts by IP and blocked after repeated failures, but successful authentication did not clear prior failures. This allowed stale failures to accumulate and could cause avoidable lockouts for legitimate clients after intermittent auth errors.

**Fix applied:**
- Added `_clear_failures(ip)` and call it immediately after successful token validation.
- Added unit test coverage for the behavior in the middleware test suite.

### 2) Async test plugin dependency drift in local environment (open)
**Severity:** Low (test infra)

The repo's test configuration expects async pytest support and coverage options, but the local environment lacks the necessary plugins (`pytest-asyncio` and `pytest-cov`).

**Impact:**
- Direct execution of async-marked test modules fails in this environment unless options are overridden.

**Recommendation:**
- Ensure CI and local developer bootstrap include `pytest-asyncio` and `pytest-cov` to keep test behavior consistent.

## Additional observations
- JWT validation correctly rejects `alg=none` and enforces `aud`/`iss` for both local and JWKS paths.
- Token blacklist lookup checks memory and Redis before validation.
- Tenant mismatch guard is present and returns `E4004` on tenant/path mismatch.

## Validation commands run
- `pytest -q tests/security/test_auth_security.py tests/unit/test_auth.py` (fails here due to missing pytest-cov addopts plugin)
- `pytest -q -o addopts='' tests/security/test_auth_security.py tests/unit/test_auth.py` (passes)
- `pytest -q -o addopts='' tests/unit/test_auth_and_core.py::TestAuthMiddleware` (fails here due to missing async pytest plugin)
