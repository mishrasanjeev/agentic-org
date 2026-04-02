# QA Test Summary — Finance Connector Fixes (April 2026)

## Executive Summary

4 critical issues in the finance connectors were identified and fixed. All 3 P0 issues were protocol/architecture bugs that would have caused production failures. The P1 issue was a missing end-to-end test. Additionally, 4 production-readiness components were built to enable real CA firm deployments.

**Test Results: 870 tests passing, 0 failures, ruff clean.**

---

## Issue #1 (P0): Tally Connector — Fake REST Protocol

### What Was Broken
The Tally connector sent JSON to REST-style endpoints (`/post/voucher`, `/get/ledger/balance`). Real Tally ERP uses XML/TDL protocol over HTTP — a single POST endpoint that accepts XML envelopes. The connector would have failed against any real Tally installation.

### Root Cause
The connector was auto-generated from a template that assumes REST/JSON for all connectors. Tally's non-standard protocol was not accounted for.

### What Was Fixed
| File | Change |
|------|--------|
| `connectors/framework/base_connector.py` | Added `_post_xml()` method for XML-over-HTTP communication |
| `connectors/finance/tally.py` | Complete rewrite — builds TDL XML envelopes (`_tdl_request`, `_import_request`), sends XML via `_post_xml`, parses XML responses. Auth changed from `tdl_rest` to `tdl_xml`, base URL fixed to `http://localhost:9000` |
| `scripts/generate_connectors.py` | Updated Tally spec: auth_type → `tdl_xml`, base_url → `http://localhost:9000` |

### How to Verify
```bash
# Unit test — verifies Tally uses XML, not JSON
pytest tests/e2e/test_ca_firm_workflow.py::TestCAFirmWorkflowE2E::test_step4_tally_uses_xml_not_json -v

# Unit test — verifies voucher posting via TDL XML
pytest tests/e2e/test_ca_firm_workflow.py::TestCAFirmWorkflowE2E::test_step4_post_voucher_to_tally -v
```

### Test Cases
| # | Test | What It Verifies |
|---|------|-----------------|
| 1 | `test_step4_tally_uses_xml_not_json` | `auth_type == "tdl_xml"`, `base_url == "http://localhost:9000"` |
| 2 | `test_step4_post_voucher_to_tally` | Voucher posting builds XML envelope, calls `_post_xml`, parses response to dict with `CREATED == "1"` |
| 3 | `test_direct_mode_by_default` | No bridge config → direct localhost mode |
| 4 | `test_bridge_mode_when_configured` | With bridge config → routes through bridge |
| 5 | `test_send_xml_direct_mode` | Direct mode calls `_post_xml` |
| 6 | `test_send_xml_bridge_mode` | Bridge mode POSTs to bridge URL, parses JSON-wrapped XML response |
| 7 | `test_bridge_error_raises` | Bridge error response raises `RuntimeError` |

---

## Issue #2 (P0): Banking AA — Payment Tools on Read-Only Connector

### What Was Broken
The Banking AA connector exposed 4 payment tools (`initiate_neft`, `initiate_rtgs`, `add_beneficiary`, `cancel_payment`). Account Aggregator is an RBI-regulated read-only framework. These endpoints don't exist on the Finvu AA API — calls would have failed in production and represent a regulatory architecture violation.

### Root Cause
Connector auto-generation conflated "banking" with "payments". AA is data aggregation only — payment initiation belongs on a separate payment gateway (e.g., PineLabs Plural).

### What Was Fixed
| File | Change |
|------|--------|
| `connectors/finance/banking_aa.py` | Removed 4 payment tools. Added `request_consent` and `fetch_fi_data` for proper AA consent flow. Now 5 tools: 3 read-only + 2 consent |
| `core/langgraph/agents/ap_processor.py` | Removed `initiate_neft` from AP Processor tools (was 5, now 4) |
| `api/v1/agents.py` | Same removal from default tools map |
| `scripts/generate_connectors.py` | Updated banking_aa spec to 3 read-only tools |
| `tests/unit/test_langgraph_runtime.py` | Fixed assertion: `len(AP_PROCESSOR_TOOLS) == 4` |

### How to Verify
```bash
# Verify no payment tools exist
pytest tests/e2e/test_ca_firm_workflow.py::TestCAFirmWorkflowE2E::test_step2_aa_has_no_payment_tools -v

# Verify AP Processor tool count
pytest tests/unit/test_langgraph_runtime.py::TestApProcessor::test_default_tools -v
```

### Test Cases
| # | Test | What It Verifies |
|---|------|-----------------|
| 1 | `test_step2_aa_has_no_payment_tools` | `initiate_neft`, `initiate_rtgs`, `add_beneficiary`, `cancel_payment` NOT in tool registry |
| 2 | `test_default_tools` | AP Processor has exactly 4 tools, `initiate_neft` not present |
| 3 | `test_connector_without_consent_has_5_tools` | 5 tools registered: 3 read + request_consent + fetch_fi_data |
| 4 | `test_no_consent_manager_without_callback_url` | No consent manager when callback_url not configured |
| 5 | `test_consent_manager_with_callback_url` | Consent manager created when callback_url configured |
| 6 | `test_request_consent_without_config_returns_error` | Graceful error when consent flow not configured |
| 7 | `test_fetch_bank_statement_direct_mode` | Falls back to direct API without consent_id |

---

## Issue #3 (P0): GSTN Connector — Wrong URL + Broken Auth

### What Was Broken
Two bugs:
1. `base_url` was `https://gsp.adaequare.com/gsp/authenticate` — every API call became `.../authenticate/fetch/gstr2a` (wrong)
2. Auth passed DSC file path as an HTTP header (`X-DSC-Path`) instead of implementing the proper Adaequare 2-step auth flow

### Root Cause
URL: `/authenticate` is an endpoint, not a base path — copy-paste error in the generator spec.
Auth: DSC signing was stubbed out and the interim auth used header-passing as a placeholder.

### What Was Fixed
| File | Change |
|------|--------|
| `connectors/finance/gstn.py` | Fixed `base_url` to `https://gsp.adaequare.com/gsp`. Implemented 2-step Adaequare auth (POST to `/authenticate` → get session token → use in headers). Added `_sign_and_post()` for DSC-signed filing. Added `get_dsc_info()`. |
| `connectors/framework/auth_adapters.py` | Replaced DSCAdapter stub with real RSA-SHA256 PKCS#1 v1.5 signing using `cryptography` library. Added `verify_certificate()` for expiry checks. |
| `connectors/finance/gstn_sandbox.py` | New — sandbox connector with Adaequare test environment config |
| `scripts/generate_connectors.py` | Fixed GSTN base_url in generator spec |

### How to Verify
```bash
# Verify base URL is correct
pytest tests/e2e/test_ca_firm_workflow.py::TestCAFirmWorkflowE2E::test_step3_gstn_base_url_correct -v

# Verify auth flow
pytest tests/integration/test_gstn_sandbox.py::TestGstnAuthFlow -v

# Verify DSC signing
pytest tests/integration/test_gstn_sandbox.py::TestDSCSigning -v

# Verify filing with DSC
pytest tests/integration/test_gstn_sandbox.py::TestGstnFilingWithDSC -v
```

### Test Cases
| # | Test | What It Verifies |
|---|------|-----------------|
| 1 | `test_step3_gstn_base_url_correct` | `base_url == "https://gsp.adaequare.com/gsp"`, no `/authenticate` |
| 2 | `test_authenticate_gets_session_token` | POSTs to `/authenticate`, sets `auth-token`, `aspid`, `gstin` headers |
| 3 | `test_sign_request_produces_valid_signature` | RSA-SHA256 signature is 256 bytes (RSA-2048) |
| 4 | `test_sign_and_get_headers` | Returns `X-DSC-Signed: true` + `X-DSC-Signature` |
| 5 | `test_verify_certificate_details` | Returns subject, issuer, expiry, `is_expired: false` |
| 6 | `test_wrong_password_raises` | Clear error: "wrong password or corrupt file" |
| 7 | `test_missing_file_raises` | Clear error: "not found" |
| 8 | `test_file_gstr3b_signs_with_dsc` | GSTR-3B filing includes DSC signature in headers |
| 9 | `test_file_gstr3b_without_dsc_falls_back` | Filing works without DSC (sandbox mode) |
| 10 | `test_get_dsc_info` | Certificate inspection returns correct details |
| 11 | `test_sandbox_base_url` | Sandbox uses `test/enriched` URL |
| 12 | `test_sandbox_default_gstin` | Default GSTIN is 15 chars and in sandbox list |

---

## Issue #5 (P1): Missing E2E Test for CA Workflow

### What Was Missing
No test validated the complete CA firm pipeline: invoice → bank reconcile → GSTN → Tally.

### What Was Built
`tests/e2e/test_ca_firm_workflow.py` — 8 tests covering the full pipeline with mocked connector responses.

### Test Cases
| # | Test | Pipeline Stage |
|---|------|---------------|
| 1 | `test_step1_create_invoice` | Invoice creation via Zoho Books |
| 2 | `test_step2_fetch_and_reconcile` | Bank statement fetch + amount matching |
| 3 | `test_step2_aa_has_no_payment_tools` | Regression: no payment tools on AA |
| 4 | `test_step3_push_gstr1` | GSTR-1 push returns success |
| 5 | `test_step3_gstn_base_url_correct` | Regression: URL doesn't include /authenticate |
| 6 | `test_step4_post_voucher_to_tally` | Tally voucher import returns CREATED=1 |
| 7 | `test_step4_tally_uses_xml_not_json` | Regression: Tally uses tdl_xml auth |
| 8 | `test_full_pipeline_data_flows` | Same invoice ID + amount flows through all 4 stages |

---

## Production Components Built

### Tally Bridge (5 tests)
| # | Test | What It Verifies |
|---|------|-----------------|
| 1 | `test_bridge_init` | Bridge initializes with correct tally_url, not connected |
| 2 | `test_forward_to_tally` | XML forwarded via HTTP POST with Content-Type: application/xml |
| 3 | `test_health_check_returns_true_on_200` | Tally health check passes on HTTP 200 |
| 4 | `test_health_check_returns_false_on_connect_error` | Health check fails gracefully on connection error |
| 5 | `test_handle_xml_request` | Processes request, sends response with request_id correlation |

### AA Consent Flow (5 tests)
| # | Test | What It Verifies |
|---|------|-----------------|
| 1 | `test_create_consent_request` | Creates consent with Finvu, returns handle + redirect URL |
| 2 | `test_handle_consent_callback_approved` | Callback updates status to APPROVED |
| 3 | `test_handle_consent_callback_rejected` | Callback updates status to REJECTED |
| 4 | `test_unknown_consent_handle` | Unknown handle returns error |
| 5 | `test_get_consent_status` | Returns correct status and consent_id |

### AA Consent Types (4 tests)
| # | Test | What It Verifies |
|---|------|-----------------|
| 1 | `test_consent_request_defaults` | Default fetch_type=ONETIME, consent_mode=VIEW |
| 2 | `test_consent_status_enum` | All 5 status values work |
| 3 | `test_fi_type_enum` | 13+ financial information types |
| 4 | `test_purpose_code_enum` | RBI purpose codes 101-105 |

---

## Full Test Inventory

| Test File | Tests | Category |
|-----------|-------|----------|
| `tests/e2e/test_ca_firm_workflow.py` | 8 | E2E pipeline |
| `tests/unit/test_production_components.py` | 26 | Bridge, consent, routing |
| `tests/integration/test_gstn_sandbox.py` | 15 | DSC, GSTN auth, sandbox |
| `tests/unit/test_langgraph_runtime.py` | 1 fix | AP Processor tools |
| **Total new/modified tests** | **50** | |
| **Total test suite** | **870** | All passing |
