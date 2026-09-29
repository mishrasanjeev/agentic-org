## Hosted versus institution-managed

The hosted workspace and a self-managed deployment share product concepts, but operational responsibilities differ. A bank-managed environment must own network, identities, keys, database, backups, model/provider access, workers, updates, security monitoring and recovery. Self-hosting alone does not establish regulatory compliance or data residency.

For most business users, use the approved hosted or institution-provided URL. This guide is for IT/platform teams, not a requirement before completing [Your first agent](/docs/first-agent).

## Local learning environment

The repository provides a Docker Compose development stack and GNU Make targets. With reviewed prerequisites installed:

```bash
git clone https://github.com/mishrasanjeev/agentic-org.git
cd agentic-org
make dev
make seed
```

The current local quickstart binds the console to `http://127.0.0.1:3000` and API to `http://127.0.0.1:8000`. It includes local data services and development verification, identity and model stubs. The default fake/scripted model responses are explicitly synthetic; this environment does not prove a real provider, bank integration or customer transaction.

Use `make ps` for service status, `make logs` for logs, `make test`/`make check` for repository checks and `make e2e` for browser tests. `make down` preserves data; `make clean` deletes development volumes. Do not run destructive cleanup against a production environment.

## Prepare production separately

| Responsibility | Required decision/evidence |
| --- | --- |
| Identity | Named users, role/scope model, SSO/SCIM where configured |
| Network | Approved ingress/egress, TLS, provider endpoints and private access |
| Database | PostgreSQL/pgvector, tenant isolation, migrations, backup/restore proof |
| Runtime | API/UI, Redis, workers/schedulers, OCR and browser dependencies |
| Models | Allowed endpoints, credentials, retention, cost and region |
| Integrations | Bank/provider contracts, scopes, test accounts and ownership |
| Operations | Monitoring, alert ownership, capacity tests and rollback |

Do not copy development placeholder secrets, fake-model flags or seed identities into production. Air-gapped deployment has additional dependency/model/image and outbound-integration limits; review the operator guide before promising disconnected capability.

## Release handoff

Use the repository's reviewed deployment path and migration-first process. Verify exact commit, migration completion, API/UI revisions, traffic, health, database/Redis and required CI. Then test the institution's actual authorized flow with approved synthetic data. A successful build is not a completed deployment or provider canary.

## User documentation domain

The public user manual is available under `/docs` in the UI build. `docs.agenticorg.ai` should be a DNS **subdomain**, not a separate nameserver delegation by default. Hosting/TLS must route that hostname to the reviewed UI/static documentation service before publishing the link. The repository's documentation-domain runbook explains the release checks and ownership; no DNS change is made by reading this guide.
