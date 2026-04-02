# Connector Production Readiness Guide — All 54 Connectors

## Overview

This document categorizes every connector by production readiness and lists exactly what's needed to take each one live. Connectors are rated:

- **READY** — API is standard, auth is implemented, can go live with just credentials
- **SANDBOX FIRST** — Has sandbox/test environment, should validate there before production
- **NEEDS WORK** — Protocol or auth needs real implementation beyond the current stub
- **BLOCKED** — Requires external registration, legal agreement, or government portal access

---

## Finance Connectors (13)

| Connector | Status | What's Needed to Go Live |
|-----------|--------|--------------------------|
| **Stripe** | READY | API key from Stripe Dashboard → Settings → API Keys. Standard REST/JSON. Test with `sk_test_*` key first, then switch to `sk_live_*`. |
| **QuickBooks** | READY | OAuth2 app registered at developer.intuit.com. Get client_id + client_secret. Sandbox available at `sandbox-quickbooks.api.intuit.com`. |
| **Zoho Books** | READY | OAuth2 app at api-console.zoho.in. Get client_id + client_secret + refresh_token. India datacenter: `books.zoho.in`. |
| **Tally** | READY | Fixed in this sprint. Requires Tally bridge agent on CA's machine. Tally XML server must be enabled (port 9000). Bridge handles tunneling. |
| **GSTN** | SANDBOX FIRST | Fixed in this sprint. Register with Adaequare as ASP. Test in sandbox (`gsp.adaequare.com/test/enriched/gsp`) with test GSTINs. DSC signing implemented for filing. Production needs real aspid + DSC certificate. |
| **GSTN Sandbox** | READY | Pre-configured for testing. Use test GSTINs provided in `gstn_sandbox.py`. |
| **Banking AA** | SANDBOX FIRST | Fixed in this sprint. Register as FIU with Finvu (finvu.in). Get client_id + client_secret + FIU ID. Consent flow implemented. Each end-client must approve data sharing via Finvu consent UI. |
| **Income Tax India** | BLOCKED | Government portal (`incometax.gov.in`). Needs: (1) Registered e-Filing account, (2) Class 3 DSC linked to PAN, (3) DSC signing for return filing — uses same DSCAdapter we built. Auth flow similar to GSTN but different portal. **Action needed:** Implement proper Income Tax portal auth (OTP-based + DSC). |
| **Oracle Fusion** | SANDBOX FIRST | REST/SOAP hybrid. Needs: Oracle Cloud instance URL, username/password or OAuth2. Test with Oracle demo instance first. **Action needed:** Implement SOAP envelope support for some endpoints (currently REST-only). |
| **SAP S/4HANA** | SANDBOX FIRST | OData + OAuth2. Needs: SAP instance URL, OAuth2 client credentials. Test with SAP sandbox/trial. **Action needed:** Implement OData batch requests and $metadata parsing for schema discovery. |
| **PineLabs Plural** | READY | API key from PineLabs merchant dashboard. Standard REST/JSON. Handles payouts, payment links, refunds, settlements. Test with sandbox credentials. |
| **AA Consent Manager** | READY | Internal module — manages consent lifecycle for Banking AA. No separate credentials needed. |

### Finance Summary
- **Ready now:** 5 (Stripe, QuickBooks, Zoho Books, Tally, PineLabs)
- **Sandbox first:** 4 (GSTN, Banking AA, Oracle, SAP)
- **Needs work:** 1 (Income Tax India — auth flow)
- **Total finance tools:** 78

---

## Communications Connectors (9)

| Connector | Status | What's Needed to Go Live |
|-----------|--------|--------------------------|
| **Gmail** | READY | Google Cloud Console → Enable Gmail API → OAuth2 credentials. Needs user consent for mailbox access. Service account for org-wide. |
| **Google Calendar** | READY | Same Google Cloud project as Gmail. Enable Calendar API. OAuth2 or service account. |
| **GitHub** | READY | Personal Access Token (PAT) or GitHub App. Create at github.com/settings/tokens. Scopes: repo, issues, pull_requests. |
| **Slack** | READY | Create Slack App at api.slack.com. Get Bot Token (`xoxb-*`). Install to workspace. Scopes: chat:write, channels:read, etc. |
| **SendGrid** | READY | API key from app.sendgrid.com → Settings → API Keys. Full access or restricted. Verify sender domain first. |
| **Twilio** | READY | Account SID + Auth Token from twilio.com/console. Buy a phone number for SMS. |
| **WhatsApp** | SANDBOX FIRST | Meta Business Suite. Create WhatsApp Business API app. Get access token. **Sandbox:** Test with Meta's test phone number. **Production:** Requires business verification by Meta (2-5 days). |
| **GCS (S3-compatible)** | READY | GCP Service Account key (JSON). Create at console.cloud.google.com → IAM → Service Accounts. Grant Storage Object Admin role. |
| **LangSmith** | READY | API key from smith.langchain.com. Used for tracing/observability. Note: this is a monitoring tool, not customer-facing. |

### Comms Summary
- **Ready now:** 8
- **Sandbox first:** 1 (WhatsApp — Meta verification)
- **Total comms tools:** 54

---

## HR Connectors (8)

| Connector | Status | What's Needed to Go Live |
|-----------|--------|--------------------------|
| **Okta** | READY | SCIM + OAuth2. Create API token at `{org}.okta.com` → Security → API → Tokens. SCIM for user provisioning. |
| **Zoom** | READY | OAuth2 app at marketplace.zoom.us. Get client_id + client_secret. Scopes: meeting:read, meeting:write. |
| **Keka** | READY | API key from Keka admin → Settings → API. Standard REST. India-focused HRMS. |
| **Greenhouse** | READY | API key from Greenhouse → Configure → Dev Center → API Credential Management. Harvest API. |
| **Darwinbox** | SANDBOX FIRST | API key + OAuth2. Contact Darwinbox support for API access (not self-service). India-focused HRMS. **Action needed:** Confirm API endpoint patterns with Darwinbox — their API docs are not public. |
| **DocuSign** | READY | JWT auth. Create integration key at developers.docusign.com. Generate RSA keypair. Consent URL for user authorization. Demo environment available. |
| **LinkedIn Talent** | BLOCKED | OAuth2. Requires LinkedIn Partner Program approval. Apply at linkedin.com/developers. Review takes 2-4 weeks. Restricted API access. **Not self-service.** |
| **EPFO** | BLOCKED | Government portal. Needs: Establishment code, DSC (same DSCAdapter). **Action needed:** Implement EPFO portal auth flow (captcha + OTP + DSC). Government portals often require browser-like interaction — may need headless browser approach. |

### HR Summary
- **Ready now:** 5 (Okta, Zoom, Keka, Greenhouse, DocuSign)
- **Sandbox first:** 1 (Darwinbox)
- **Blocked:** 2 (LinkedIn Talent — partner approval, EPFO — government portal)
- **Total HR tools:** 48

---

## Marketing Connectors (9)

| Connector | Status | What's Needed to Go Live |
|-----------|--------|--------------------------|
| **HubSpot** | READY | OAuth2 app at app.hubspot.com → Settings → Integrations → Private Apps. Get access token. Free tier available. |
| **Salesforce** | SANDBOX FIRST | OAuth2 + SOAP (some endpoints). Create Connected App in Salesforce Setup. Use sandbox org (`test.salesforce.com`) first. **Action needed:** Implement SOAP envelope for Metadata API and some bulk operations. |
| **Google Ads** | READY | OAuth2. Create at console.developers.google.com. Enable Google Ads API. Need developer token from ads.google.com → Tools → API Center. Test with test account. |
| **Meta Ads** | READY | Same Meta Business Suite as WhatsApp. Marketing API access token. System user for server-to-server. |
| **LinkedIn Ads** | BLOCKED | OAuth2. Requires LinkedIn Marketing Developer Platform access. Apply at linkedin.com/developers. Same partner program as Talent — restricted. |
| **Buffer** | READY | OAuth2 at buffer.com/developers. Simple API. Free tier for testing. |
| **Ahrefs** | READY | API token from ahrefs.com → Account → API. Paid plan required (Lite+). Rate-limited. |
| **Brandwatch** | SANDBOX FIRST | OAuth2. Contact Brandwatch for API access — enterprise sales process. **Action needed:** Confirm endpoint patterns — API is not publicly documented for all features. |
| **Mixpanel** | READY | API key + secret from mixpanel.com → Settings → Project Settings. Service account for server-side. |
| **Bombora** | SANDBOX FIRST | API key from Bombora. Surge intent data for B2B accounts. Contact Bombora sales for API access. Real paths: `/surge/companies`, `/surge/topics`. |
| **G2** | SANDBOX FIRST | API token from G2 developer portal. Buyer intent signals + product reviews. Real paths: `/intent-signals`, `/products/{slug}/reviews`. |
| **TrustRadius** | SANDBOX FIRST | Bearer token from TrustRadius. Buyer intent + comparison traffic. Real paths: `/intent/buyer-activity`, `/products/{id}/reviews`. |

### Marketing Summary
- **Ready now:** 6 (HubSpot, Google Ads, Meta Ads, Buffer, Ahrefs, Mixpanel)
- **Sandbox first:** 5 (Salesforce, Brandwatch, Bombora, G2, TrustRadius)
- **Blocked:** 1 (LinkedIn Ads — partner approval)
- **Total marketing tools:** 53

---

## Operations Connectors (7)

| Connector | Status | What's Needed to Go Live |
|-----------|--------|--------------------------|
| **Jira** | READY | OAuth2 or API token. Create token at id.atlassian.com → Security → API Tokens. Cloud only — no on-prem support in current impl. |
| **Confluence** | READY | Same Atlassian API token as Jira. REST API v2. |
| **Zendesk** | READY | API token from `{subdomain}.zendesk.com` → Admin → Apps → API. Email + token auth. |
| **ServiceNow** | SANDBOX FIRST | REST + OAuth2. Create OAuth Application in ServiceNow instance. Use Personal Developer Instance (free) for testing at developer.servicenow.com. **Action needed:** Some table APIs require specific roles — confirm scoped app permissions. |
| **PagerDuty** | READY | API key from pagerduty.com → Configuration → API Access Keys. REST v2. |
| **MCA Portal** | BLOCKED | Government portal (Ministry of Corporate Affairs). Needs: DIN, DSC, MCA registration. **Action needed:** Same challenge as EPFO — government portal auth with captcha/OTP. Uses DSCAdapter for signing. |
| **Sanctions API** | READY | API key from sanctions.io. KYC/AML screening. Free tier available for testing. |

### Ops Summary
- **Ready now:** 5 (Jira, Confluence, Zendesk, PagerDuty, Sanctions)
- **Sandbox first:** 1 (ServiceNow)
- **Blocked:** 1 (MCA Portal — government portal)
- **Total ops tools:** 40

---

## Overall Production Readiness

| Category | Ready | Sandbox First | Needs Work | Blocked | Total |
|----------|-------|---------------|------------|---------|-------|
| Finance | 5 | 4 | 1 | 0 | 10* |
| Communications | 8 | 1 | 0 | 0 | 9 |
| HR | 5 | 1 | 0 | 2 | 8 |
| Marketing | 6 | 2 | 0 | 1 | 9 |
| Operations | 5 | 1 | 0 | 1 | 7 |
| **Total** | **29** | **9** | **1** | **4** | **43** |

*\*Including AA Consent Manager and GSTN Sandbox as separate entries.*

**29 connectors (67%) are ready to go live today with just credentials.**

---

## Priority Actions for Full Production Coverage

### Tier 1 — Quick Wins (1-2 days each)
1. **Income Tax India** — Implement OTP+DSC auth flow (DSCAdapter already built)
2. **Oracle Fusion** — Add SOAP envelope support for mixed REST/SOAP endpoints
3. **SAP S/4HANA** — Add OData batch and $metadata parsing

### Tier 2 — External Dependencies (1-2 weeks)
4. **LinkedIn Talent/Ads** — Apply for Partner Program (2-4 week review)
5. **Darwinbox** — Contact support for API documentation + sandbox access
6. **Brandwatch** — Enterprise sales process for API access
7. **WhatsApp** — Submit Meta business verification

### Tier 3 — Government Portals (2-4 weeks each)
8. **EPFO** — Research headless browser approach for captcha/OTP flows
9. **MCA Portal** — Same approach as EPFO; DSC signing already works

### Recommendation
Focus on **Tier 1** first — these 3 fixes cover the most-requested enterprise integrations (Income Tax for CA firms, Oracle/SAP for enterprises). The DSCAdapter we built for GSTN works identically for Income Tax and MCA, so those are mostly auth flow work.
