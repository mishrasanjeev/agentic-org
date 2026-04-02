# CA Firm Setup Guide — AgenticOrg AI Agent Platform

## Overview

This guide covers everything needed to demo and deploy the AgenticOrg AI agent platform for a Chartered Accountant firm. The core workflow automates: **Invoice Processing → Bank Reconciliation → GST Filing → Tally Sync**.

---

## Credentials Checklist

### From the CA Firm

| # | Credential | Purpose | Who Provides | Notes |
|---|-----------|---------|-------------|-------|
| 1 | **Tally Prime license details** | Verify Tally version (Prime 4.x or ERP 9) | CA firm IT | Must have ODBC/XML server enabled on port 9000 |
| 2 | **Tally company name** | Target company file for voucher posting | CA firm | Exact name as it appears in Tally |
| 3 | **Tally machine IP** | Bridge agent target (usually localhost) | CA firm IT | If multi-machine setup, provide Tally server IP |
| 4 | **Adaequare GSP credentials** | GSTN filing via GSP | CA firm or Adaequare | aspid (API key), username, password |
| 5 | **GSTIN numbers** | All client GSTINs for filing | CA firm | 15-character GSTINs for each client entity |
| 6 | **DSC (.pfx) file + password** | Digital signature for GST/IT filing | CA firm | Class 2 or Class 3 DSC, PAN-linked |
| 7 | **Bank Account Aggregator consent** | Read bank statements via Finvu AA | CA firm's clients | Each client must approve via Finvu consent UI |
| 8 | **Zoho Books / Tally Cloud API key** | Invoice data source (if using cloud accounting) | CA firm | OAuth2 or API key depending on provider |

### Platform Credentials (We Provide)

| # | Credential | Purpose |
|---|-----------|---------|
| 1 | AgenticOrg API key | Platform access |
| 2 | Tenant ID | Multi-tenant isolation |
| 3 | Bridge ID + Token | Tally bridge authentication |
| 4 | Finvu FIU ID + Client credentials | Account Aggregator registration |

### Government Portal Access (CA Firm Arranges)

| Portal | Required For | Registration |
|--------|-------------|-------------|
| GSTN (via Adaequare GSP) | GST return filing | Register at adaequare.com, get sandbox → production credentials |
| Income Tax e-Filing | TDS returns (26Q/24Q) | Existing login, DSC required |
| EPFO | PF compliance | Establishment code + DSC |
| MCA Portal | ROC filings | DIN + DSC |

---

## Setup Timeline

| Phase | Duration | Activities |
|-------|----------|-----------|
| **Day 1: Kickoff** | 2 hours | Collect credentials, verify Tally version, explain consent flow |
| **Day 2-3: Platform Setup** | 1-2 days | Create tenant, configure connectors, install Tally bridge |
| **Day 4-5: Shadow Mode** | 2 days | Run agents in shadow mode — process real data without taking action |
| **Day 6-7: Validation** | 2 days | CA firm validates shadow results against their manual work |
| **Day 8: Go-Live** | Half day | Promote agents to active, enable HITL approvals |
| **Ongoing** | Continuous | Monitor dashboards, tune confidence thresholds |

**Total: 5-8 business days from kickoff to go-live.**

---

## Step-by-Step Setup

### Step 1: Install the Tally Bridge (30 minutes)

The bridge agent runs on the CA's machine where Tally is installed. It tunnels XML/TDL requests from our cloud platform to their local Tally.

```bash
# Install the bridge
pip install agenticorg-bridge

# Register the bridge with our platform
agenticorg-bridge register \
  --api-key <AGENTICORG_API_KEY> \
  --tenant-id <TENANT_ID>

# Output:
#   Bridge ID:    abc-123-def
#   Bridge Token: <token>

# Start the bridge (runs in background)
agenticorg-bridge start \
  --cloud-url wss://app.agenticorg.ai/api/v1/ws/bridge \
  --bridge-id abc-123-def \
  --bridge-token <token> \
  --tally-port 9000
```

**Verify:**
```bash
agenticorg-bridge status
#   Tally: REACHABLE (HTTP 200)
#   Protocol: XML/TDL responding correctly
```

**Pre-requisite:** In Tally Prime, enable the XML server:
- Gateway of Tally → F12 (Configure) → Data Configuration
- Set "Allow XML Server" to Yes
- Port: 9000 (default)

### Step 2: Configure GSTN Connector (15 minutes)

```python
# Via API or dashboard
POST /api/v1/connectors/configure
{
  "connector": "gstn",
  "config": {
    "api_key": "<ADAEQUARE_ASPID>",
    "username": "<GSP_USERNAME>",
    "password": "<GSP_PASSWORD>",
    "gstin": "<PRIMARY_GSTIN>",
    "dsc_path": "/path/to/dsc.pfx",        # For filing
    "dsc_password": "<DSC_PASSWORD>"
  }
}
```

**Verify DSC before filing season:**
```python
GET /api/v1/connectors/gstn/dsc-info
# Returns: subject, issuer, expiry date, days until expiry
```

### Step 3: Set Up Bank Account Aggregator (20 minutes)

Each of the CA firm's clients must approve data sharing:

```python
# Create consent request for a client
POST /api/v1/aa/consent/request
{
  "customer_vua": "client@finvu",
  "fi_types": ["DEPOSIT"],
  "purpose_code": 103,
  "from_date": "2026-04-01",
  "to_date": "2026-06-30"
}
# Returns: { "consent_handle": "...", "redirect_url": "https://finvu.in/consent/..." }
```

Send the `redirect_url` to the client. They approve on Finvu's consent UI. Once approved, our webhook receives the callback automatically.

### Step 4: Configure Agent Workflows (30 minutes)

Create the CA firm workflow via the dashboard or API:

```python
POST /api/v1/workflows
{
  "name": "CA Monthly Processing",
  "steps": [
    {"agent": "ar_collections", "action": "create_invoices"},
    {"agent": "recon_agent", "action": "bank_reconciliation"},
    {"agent": "tax_compliance", "action": "gstr1_filing"},
    {"agent": "ap_processor", "action": "post_to_tally"}
  ],
  "schedule": "0 9 28 * *",  // 28th of every month at 9 AM
  "hitl_threshold": 500000     // Escalate above 5 lakhs
}
```

### Step 5: Run Shadow Mode (2 days)

Shadow mode processes every transaction in parallel with the CA firm's existing manual process, without taking any action:

```python
POST /api/v1/agents/<agent_id>/shadow
{
  "duration_hours": 48,
  "compare_with": "manual"
}
```

Dashboard shows: match rate, discrepancies, confidence scores.

### Step 6: Go Live

Once the CA firm validates shadow results:
```python
POST /api/v1/agents/<agent_id>/promote
{
  "mode": "active",
  "hitl_condition": "total > 500000 OR confidence < 0.88"
}
```

---

## Demo Script (30 minutes)

For a quick demo to a prospective CA firm:

1. **Show the problem** (2 min): Open Tally, show manual voucher entry, GSTN portal login, bank statement download
2. **Create an invoice** (3 min): Use Zoho Books connector to create a test invoice
3. **Bank reconciliation** (5 min): Fetch bank statement via AA, show auto-matching
4. **GST filing** (5 min): Push GSTR-1 data, show filing status
5. **Tally sync** (5 min): Post voucher to Tally via bridge, verify in Tally
6. **HITL demo** (5 min): Show high-value invoice triggering CFO approval
7. **Dashboard** (5 min): Show agent activity, confidence metrics, ROI calculator

---

## Troubleshooting

| Issue | Resolution |
|-------|-----------|
| Bridge can't reach Tally | Verify Tally XML server is enabled (F12 → Data Config → Allow XML Server = Yes) |
| GSTN auth fails | Check aspid, username, password. Try sandbox first: `https://gsp.adaequare.com/test/enriched/gsp` |
| DSC signing fails | Verify .pfx password, check certificate expiry with `GET /connectors/gstn/dsc-info` |
| AA consent times out | Client must complete approval on Finvu UI within 5 minutes |
| Tally voucher rejected | Check company name matches exactly, verify voucher type exists in Tally |

---

## Security Notes

- All credentials stored in GCP Secret Manager (never in code or config files)
- DSC private keys never leave the CA's machine (signing happens locally via bridge)
- AA consent is time-bound and revocable by the client at any time
- HITL ensures no high-value transaction proceeds without human approval
- All agent actions are audit-logged with tenant isolation
