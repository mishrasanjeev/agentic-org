# Brutal Security Audit Plan - 2026-06-13

## Objective

Audit the production dependency graph, optional dependency graph, CI security
gates, deploy helper, and secret exposure surface from an isolated worktree.
Fix validated vulnerabilities end to end, then add regression gates so the same
class does not reopen.

## Validated Findings

| ID | Finding | Evidence | Fix |
| --- | --- | --- | --- |
| SEC-2026-06-13-001 | Base project dependency resolution pulled `torch 2.12.0`, and `pip-audit .` reported `CVE-2025-3000` with no fixed version. | `pip-audit .` before remediation. RouteLLM and FlagEmbedding both own PyTorch in optional ML paths. | Removed PyTorch-owning packages from base/v4 production dependencies. Kept BGE-M3 in-process loader behind explicit `bge-m3` extra and kept RouteLLM absent so router falls back to heuristic mode. |
| SEC-2026-06-13-002 | Base project dependency resolution pulled vulnerable Pillow 11.x through `fastembed<0.8`. | `pip-audit .` reported Pillow CVEs fixed by `Pillow>=12.2.0`. | Upgraded production embedding dependency to `fastembed>=0.8.0` and added a direct `pillow>=12.2.0` patched floor. |
| SEC-2026-06-13-003 | v4 optional dependencies pulled vulnerable Pillow 10.4.0 through `composio-core==0.7.21` (`Pillow<11`). | `pip-audit -r requirements-v4.txt` reported active Pillow CVEs. Wheel metadata confirmed `Requires-Dist: Pillow<11,>=10.2.0`. | Removed `composio-core` from production v4 extras and `requirements-v4.txt`. The Composio API remains guarded and returns 503 until upstream supports patched Pillow. |
| SEC-2026-06-13-004 | Security CI collected findings but did not fail. | `.github/workflows/security-scan.yml` used `continue-on-error: true`; deploy workflow warned through `pip-audit`. | Nightly and deploy security checks now fail closed on pip-audit, Bandit, npm audit, and Trivy high/critical findings. |
| SEC-2026-06-13-005 | npm audit covered only `ui`, leaving `mcp-server` and `sdk-ts` unscanned. | Workflow only ran `cd ui && npm audit`. | Nightly npm audit now runs a matrix over `ui`, `mcp-server`, and `sdk-ts`. |
| SEC-2026-06-13-006 | Manual Cloud Run deploy helper conflated Cloud Run region and Artifact Registry region. | Production Cloud Run path is in `asia-southeast1`; registry default historically uses `asia-south1`. | Added separate `CLOUD_RUN_REGION`, `GAR_REGION`, `GAR_HOST`, and GAR registry derivation. |
| SEC-2026-06-13-007 | Runtime JWT stack pulled `ecdsa` through `python-jose`, and Trivy flagged `CVE-2024-23342`. | Local image scan reported `ecdsa 0.19.2` as HIGH. Prior code documented it as residual instead of removing the dependency path. | Migrated runtime JWT creation/validation and tests to `PyJWT[crypto]`; removed `python-jose` from production manifests; added dependency gates forbidding `python-jose` and `ecdsa`. |
| SEC-2026-06-13-008 | Runtime image carried avoidable OS findings from `curl`, libcurl, libssh2, and a fixable OpenSSL package. | Local Trivy scan before Dockerfile remediation reported 18 Debian findings plus the `ecdsa` finding. | Removed runtime `curl`, replaced the healthcheck with Python `urllib`, and added `apt-get upgrade` in the runtime stage so OpenSSL updates to the fixed Debian package. |
| SEC-2026-06-13-009 | Fresh Docker database migration failed before API startup. | `docker run ... python scripts/alembic_migrate.py` failed on a clean Postgres DB because the wrapper tried to run the Alembic chain directly from an empty schema even though the chain assumes the historical raw-SQL baseline. | `scripts/alembic_migrate.py` now bootstraps the ORM baseline, stamps `v480_baseline`, upgrades to head, and fails closed on unknown partial schemas. Integration coverage now exercises empty, legacy, and already-managed DB paths. |

## Permanent Rules

- No production base or v4 dependency may pull `torch`, `FlagEmbedding`,
  `routellm`, `litellm`, or `composio-core` until the owning upstream packages
  have auditable fixed dependency graphs.
- Pillow must remain directly pinned at `>=12.2.0` in production metadata.
- Composio must fail closed through the existing import guard until its SDK can
  install with patched Pillow.
- Security workflows must fail the job on findings. Advisory-only security jobs
  are treated as broken gates.
- All Node workspaces with lockfiles must be audited, not just the frontend.
- Manual deploy scripts must not reuse one region variable for multiple cloud
  resources that can live in different regions.
- `python-jose` and `ecdsa` are not acceptable production dependencies; JWT
  handling stays on `PyJWT[crypto]`.
- Docker verification must include fresh DB migration, normal API startup,
  liveness/readiness checks, image dependency assertions, and Trivy scanning.
- Trivy must fail on fixable HIGH/CRITICAL findings. Debian base CVEs with no
  fixed package are tracked as residual base-image exposure until a patched
  base digest is available.

## Regression Suite Additions

- `tests/regression/test_security_audit_20260613_dependency_gates.py`
  verifies patched Pillow floor, no PyTorch-owning packages in production
  dependency sets, explicit-only BGE-M3 extra, v4 unsafe package exclusions, and
  fail-closed security workflow behavior. It also blocks `python-jose`/`ecdsa`,
  pins the Trivy fixed-vulnerability gate, and prevents reintroducing runtime
  `curl` just for healthchecks.
- `tests/integration/test_alembic_e2e.py` now covers fresh empty DB bootstrap,
  legacy DB stamping, and already-managed DB idempotence.
- `tests/regression/test_security_pr_a_pins.py` now enforces patched Pillow
  rather than documenting a residual.
- `tests/unit/test_v490_reqs.py` now requires guarded Composio behavior instead
  of requiring a vulnerable SDK in the runtime image.

## Verification Commands

Run from `C:\tmp\agentic-org-security-audit-20260613`:

```powershell
python -m pip_audit -s pypi --desc --timeout 60 --progress-spinner off .
python -m pip_audit -s pypi --desc --timeout 60 --progress-spinner off -r requirements.txt
python -m pip_audit -s pypi --desc --timeout 60 --progress-spinner off -r requirements-v4.txt
python -m bandit -r core api auth connectors workflows -ll
python -m pytest tests/regression/test_security_audit_20260613_dependency_gates.py tests/regression/test_security_pr_a_pins.py::test_pillow_cve_floor_is_enforced_in_production_dependencies tests/unit/test_v490_reqs.py::TestREQ01ComposioRuntime -q
python -m pytest tests/integration/test_alembic_e2e.py -q
python -m pytest tests/unit/test_auth_and_core.py::TestCreateAccessToken tests/unit/test_auth_and_core.py::TestValidateLocalToken tests/security/test_auth_security_full.py::TestSECAUTH003 tests/security/test_auth_security_full.py::TestSECAUTH004 -q
```

Node workspaces:

```powershell
npm audit --audit-level=moderate
```

Run separately in `ui`, `mcp-server`, and `sdk-ts`.

Docker/local workstation:

```powershell
& "C:\Program Files\Docker\Docker\resources\bin\docker.exe" build -t agenticorg-api:security-audit .
& "C:\Program Files\Docker\Docker\resources\bin\docker.exe" run --rm agenticorg-api:security-audit python -c "import importlib.util, PIL, jwt; forbidden=['torch','FlagEmbedding','composio','routellm','litellm','jose','ecdsa']; found=[p for p in forbidden if importlib.util.find_spec(p) is not None]; assert not found, found; assert tuple(int(x) for x in PIL.__version__.split('.')[:2]) >= (12, 2); print('image-deps-ok', PIL.__version__, jwt.__version__)"
& "C:\Program Files\Docker\Docker\resources\bin\docker.exe" compose -p agenticorg_sec_audit_20260613 -f docker-compose.yml -f docker-compose.local-e2e.yml up -d postgres redis minio
& "C:\Program Files\Docker\Docker\resources\bin\docker.exe" run --rm --network agenticorg_sec_audit_20260613_default -e AGENTICORG_ENV=development -e AGENTICORG_DB_URL=postgresql+asyncpg://agenticorg:agenticorg_dev@postgres:5432/agenticorg -e AGENTICORG_REDIS_URL=redis://redis:6379/0 -e AGENTICORG_SECRET_KEY=agenticorg-dev-only-do-not-use-in-production agenticorg-api:security-audit python scripts/alembic_migrate.py
& "C:\Program Files\Docker\Docker\resources\bin\docker.exe" run -d --name agenticorg-api-security-audit-local --network agenticorg_sec_audit_20260613_default -p 18080:8000 -e AGENTICORG_ENV=development -e AGENTICORG_DB_URL=postgresql+asyncpg://agenticorg:agenticorg_dev@postgres:5432/agenticorg -e AGENTICORG_REDIS_URL=redis://redis:6379/0 -e AGENTICORG_STORAGE_BUCKET=agenticorg-docs-dev -e AGENTICORG_STORAGE_ENDPOINT=http://minio:9000 -e AGENTICORG_SECRET_KEY=agenticorg-dev-only-do-not-use-in-production -e AGENTICORG_PII_MASKING=true agenticorg-api:security-audit
curl.exe -fsS http://localhost:18080/api/v1/health/liveness
curl.exe -fsS http://localhost:18080/api/v1/health
& "C:\Program Files\Docker\Docker\resources\bin\docker.exe" run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy:latest image --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --no-progress agenticorg-api:security-audit
```

## Status

All listed fixes were implemented in the isolated worktree. Python dependency
audits, Bandit, targeted regression tests, npm audits for `ui`, `mcp-server`,
and `sdk-ts`, Docker image build, fresh-DB migration, API liveness/readiness,
Docker healthcheck, and Trivy fixed-vulnerability scan are green after
remediation.

Residual container exposure remains only for Debian 13.5 base packages where
Trivy reports no fixed package yet: `perl-base` and `ncurses` family
(`libncursesw6`, `libtinfo6`, `ncurses-base`, `ncurses-bin`). These are not
ignored as "safe"; they are tracked as unfixed base-image residuals and must be
retired by the next patched Python base digest or a future distroless/runtime
base migration.
