# Repository Guardrails

Keep changes small, explicit, and verifiable. Follow existing patterns before adding a new abstraction.

## Authority and tenancy

- Derive tenant, role, and privilege from authenticated server-side context. Refuse missing or ambiguous authority.
- Check authorization at the backend action boundary. A UI gate alone is insufficient.
- Bind database reads and writes to the authenticated tenant and test cross-tenant attempts.
- A missing grant, failed policy load, invalid condition, or unverifiable webhook must not authorize an action.

## Secrets, data, and runtime

- Never put credentials or personal data in logs, metrics labels, fixtures, or public documentation. Store persisted credentials encrypted or in a secret manager.
- Add a forward-only Alembic migration for every schema change. Plan backfill, rollout, and tenant isolation; do not rely on startup DDL.
- Do not use synchronous HTTP, Redis, or database clients in async handlers.
- Keep metrics labels low-cardinality; use structured logs for detailed context.
- Keep public health checks local and free of sensitive configuration details.

## Public interfaces and delivery

- Keep provider interfaces neutral. Use `mock` and `acme_kyb` in examples; commercial provider adapters belong in separate packages.
- Keep private plans, customer identities, local paths, commercial terms, and real secret values out of this public repository.
- Commit under the repository owner's configured identity without tool-credit trailers or generated-by notes.
- Reproduce a reported bug, inspect sibling paths, add a regression test that replays the failure, and rerun it after the fix.
- Verify targeted backend tests and lint for Python changes. Run `make check` and `make test` before claiming a release-ready result.
- Before a push, run `bash scripts/preflight.sh`; set `SKIP_UI=1` only for a backend-only change. Keep the working branch off `main`.
