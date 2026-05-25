# CMO Vendor-Sandbox QA Flow

Use this runbook to QA the CMO vendor-sandbox connector flow end to end. Do not paste real credentials into tickets, chat, docs, screenshots, or commit history.

## 1. Login And Open Setup

1. Login to the target tenant as an admin user.
2. Open `/dashboard/connectors`.
3. Click `CMO Sandbox Setup`.
4. Confirm the page shows four required categories: `CRM`, `Ads`, `Analytics`, and `Email`.
5. Confirm any existing DB rows show only safe metadata: provider name, DB readiness, proof scope, environment type, and boolean flags. Credential values must not appear.

## 2. Configure One Provider Per Category

Use real QA-owned sandbox credentials only.

1. CRM: choose `HubSpot Sandbox` or `Salesforce Sandbox`.
2. Ads: choose `Google Ads Test Customer`, `Meta Ads Sandbox`, or `LinkedIn Ads Sandbox`.
3. Analytics: choose `GA4 Sandbox Property`.
4. Email: choose `SendGrid Sandbox` or `Mailchimp Test Account`.
5. Fill every required field for the selected provider.
6. Click `Save CMO Sandbox Connectors`.

Expected UI result:

- Success message appears.
- All four current DB preflight cards show `DB ready`.
- Each row shows `vendor_sandbox | vendor_sandbox`.
- Each row shows `local=false mock=false`.
- No credential values are displayed after save.

## 3. Verify Stored DB Shape Locally

In PowerShell:

```powershell
Set-Location C:\tmp\agenticorg-cmo-1-1
$env:AGENTICORG_DB_URL = "postgresql+asyncpg://agenticorg:agenticorg_dev_password@localhost:5433/agenticorg"
$env:AGENTICORG_CMO_SANDBOX_TENANT_ID = "d3f0d84c-836f-4cda-8896-ce2f1623213d"
python scripts/run_weekly_report_sandbox_pilot.py --preflight-only --json
```

Required result:

- `preflight_status` is `ready`.
- `chosen_connectors.CRM.source` is `db`.
- `chosen_connectors.Ads.source` is `db`.
- `chosen_connectors.Analytics.source` is `db`.
- `chosen_connectors.Email.source` is `db`.
- Each category has `readiness_state=ready`.
- Each category has `proof_scope=vendor_sandbox`.
- Each category has `local_test_only=false`.
- Each category has `mock_or_test_double=false`.
- No category uses `source=env`.

If preflight is blocked, stop. Do not run the full proof.

## 4. Run Full Vendor-Sandbox Proof

Only run this after preflight is clean:

```powershell
python scripts/run_weekly_report_sandbox_pilot.py --format json > sandbox-proof.json
```

Inspect `sandbox-proof.json`:

- `preflight_status` is `ready`.
- `environment_type` is `vendor_sandbox`.
- `production_claim_allowed` is `false`.
- `real_vendor_claim_allowed` is `false`.
- A sandbox pass is not production proof.

Then inspect the latest DB row:

```powershell
@'
import asyncio, os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

async def main():
    engine = create_async_engine(os.environ["AGENTICORG_DB_URL"])
    async with engine.connect() as conn:
        result = await conn.execute(text("""
            SELECT proof_id, environment_type, proof_status,
                   production_claim_allowed, real_vendor_claim_allowed,
                   readiness_score, evaluated_at
              FROM weekly_report_pilot_proofs
             WHERE tenant_id = :tenant_id
             ORDER BY evaluated_at DESC
             LIMIT 1
        """), {"tenant_id": os.environ["AGENTICORG_CMO_SANDBOX_TENANT_ID"]})
        print(result.mappings().first())
    await engine.dispose()

asyncio.run(main())
'@ | python -
```

Required DB result:

- `environment_type=vendor_sandbox`.
- `production_claim_allowed=false`.
- `real_vendor_claim_allowed=false`.
- `proof_status` is `sandbox_proven`, `partial`, or `blocked` according to evidence quality. It must not silently imply real-vendor production readiness.

## 5. QA The CMO Dashboard

1. Open `/dashboard/cmo`.
2. Confirm `Marketing Connector Setup` lists CRM, Ads, Analytics, and Email.
3. Confirm configured sandbox rows show healthy or actionable status without demo-only claims.
4. Confirm any remaining field mapping, backfill, reconciliation, report-quality, approval, or workflow blockers are visible as blockers or work queue items.
5. Click connector setup CTAs. Missing connector setup should route to `/dashboard/connectors/cmo-vendor-sandbox`.
6. Confirm the dashboard never claims full autonomous CMO production readiness from vendor-sandbox proof alone.

## 6. Regression Checks

Run:

```powershell
python -m pytest tests/unit/test_cmo_vendor_sandbox_connector_config.py tests/unit/test_cmo_weekly_report_sandbox_runner.py tests/unit/test_cmo_weekly_report_pilot_persistence.py tests/unit/test_cmo_weekly_report_pilot_proof.py --no-cov -q
python -m ruff check api/v1/connectors.py core/marketing/weekly_report_sandbox_pilot.py core/marketing/weekly_report_pilot_persistence.py scripts/run_weekly_report_sandbox_pilot.py scripts/configure_cmo_vendor_sandbox_connectors.py tests/unit/test_cmo_vendor_sandbox_connector_config.py
npm --prefix ui test -- CMOVendorSandboxConnectors
npm --prefix ui test -- CMODashboard
npm --prefix ui run typecheck
python -m compileall api/v1/connectors.py core/marketing/weekly_report_sandbox_pilot.py core/marketing/weekly_report_pilot_persistence.py scripts/run_weekly_report_sandbox_pilot.py scripts/configure_cmo_vendor_sandbox_connectors.py
git diff --check
```

## 7. Production Acceptance

CMO-PROD-3F is complete only when:

- The tenant has four DB-discovered ConnectorConfig rows from the UI or helper path.
- The rows are real vendor-sandbox credentials, not placeholders.
- Preflight discovers all four categories from DB.
- No env fallback is used.
- A full proof row is inserted only after clean preflight.
- `production_claim_allowed=false` and `real_vendor_claim_allowed=false` remain false for vendor-sandbox evidence.
