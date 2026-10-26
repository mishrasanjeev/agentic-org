# MFA Challenge State

Pending sign-ins are opaque, five-minute Redis records, not session tokens.
They contain versioned user, tenant, first-factor method and security-generation
claims. Verification compares that generation to the locked, active user row.
There is no compatibility fallback for old or malformed challenge records; the
user must complete the first factor again.

The generation advances on password reset, logout-all, deactivation, MFA reset,
enrollment confirmation, disable and recovery-code replacement. These mutations
hold the user row lock through commit. Verification also holds that lock while
consuming the exact challenge payload, preparing the session and committing the
accepted second factor. This orders a concurrent reset against verification;
it does not change the existing session-token watermark policy.

All second-factor guesses share a five-attempt, five-minute account budget,
including enrollment confirmation and authenticated MFA management. The key is
bound to immutable tenant and user IDs. Fresh challenges, different first-factor
methods, different client IPs and repeated setup do not reset that budget.
Redis increments the counter and assigns its initial expiry atomically. Existing
per-IP and per-challenge controls remain in force. A valid second factor clears
the shared counter while the user lock is held. Store failures refuse verification
without issuing a session; no process-local budget is substituted.

Admin resets additionally require an active human administrator, not an API key
or delegated grant. Actor and target rows are locked in stable ID order, and
actor authority is rechecked under those locks. Self-reset uses immutable user
identity. TOTP and recovery input accepts only ASCII before normalization.

These are fail-closed authentication-policy corrections and intentionally have
no behavior flag or bypass. They do not enable any optional feature.

## Compatibility And Validation

The additive `mfa_challenge_generation` migration must precede the corrected
application. The previous application ignores that column, but pending challenges
created by previous code are rejected by corrected verification. Mixed-version
replicas would still permit the previous policy, so a partially upgraded fleet
is not evidence of enforcement. Deployment requires a separately reviewed plan
and explicit release approval; this document is not an executable rollout.

Unit regressions use synthetic persistence and cache boundaries. The isolated
PostgreSQL/Redis suite must run separately to establish transaction, locking,
counter and migration behavior. Neither set is proof of production exploitation,
provider acceptance, full-application E2E, or deployed correctness.

The `MFA Security State` workflow provisions dedicated PostgreSQL and Redis
services. Its required-mode environment makes missing test configuration fail
instead of skip. Local physical migration checks use
`MFA_MIGRATION_TEST_DB_URL` and `MFA_MIGRATION_TEST_REQUIRED=1`; router checks
use `MFA_STATE_TEST_ENABLE=1`, `MFA_STATE_TEST_REQUIRED=1`,
`MFA_STATE_TEST_PG_URL` and `MFA_STATE_TEST_REDIS_URL`. These URLs are test-only
and must never point at application databases or shared caches.
