---
name: 2026-04-24 combined bug sweep (Aishwarya + Uday/Ramesh)
description: 24-Apr-2026 sweep — 9 Aishwarya TCs + 5 Uday/Ramesh items. 14 bugs total. Four bug classes, all in one PR on fix/qa-24apr-sweep. 30 new regression tests in tests/unit/test_bug_sweep_24apr.py.
type: project
originSessionId: 50c2ef0e-fce4-4b61-9681-f2bdcd1a87db
---
## Items
- **TC_001 (reopen #2)** — report-schedules GET/POST 500. Root cause: legacy `recipients=["email-string"]` rows crashed `_to_response`; GET had no try/except. Fix: `_coerce_channel` + per-row skip + wrapped route. See `api/v1/report_schedules.py`.
- **TC_002** — KB search "Something went wrong". Fix: wrapped `/knowledge/search` in try/except; returns structured 500 pointing at `/knowledge/health`.
- **TC_003** — 18 defaults hidden after creating first custom schema. Fix: union defaults + persisted in `ui/src/pages/Schemas.tsx`.
- **TC_004/005** — view/edit blank. Symptom of TC_006.
- **TC_006** — empty `json_schema={}` accepted. Fix: `@field_validator` in `core/schemas/api.py::SchemaCreate` requires `type` or `$ref`, and `type=object` needs non-empty `properties`.
- **TC_007** — shadow accuracy 40%. Verdict: Enhancement. Floor was lowered from 0.95 → 0.80 by v487; 40% is the honest signal.
- **TC_008** — chat bubbles rendered `{'type':'text','text':...}` verbatim. Fix: `_extract_readable` recursive walker in `api/v1/chat.py`. No `str(dict)` anywhere in the path.
- **TC_009** — Hindi i18n drift. Deferred to dedicated i18n sweep PR.
- **RA-Zoho-OrgId** — missing UI field. Fix: generic "Extra config (JSON)" textarea on ConnectorCreate + ConnectorDetail, merged into `auth_config`. Covers Zoho `organization_id`, NetSuite `account`, Shopify `shop`.
- **RA-Zoho-Test** — linked to RA-Zoho-OrgId; same fix.
- **RA-ReportSched** — duplicate of TC_001.
- **UI-OAUTH-001** — OAuth2 Edit missing Client Secret + Refresh Token. Fix: added both fields to `ui/src/pages/ConnectorDetail.tsx` OAuth2 branch.
- **UI-HEALTH-404** — Gmail test reported healthy on HTTP 404. Fix: `BaseConnector.health_check` now gates healthy on 2xx/3xx; 4xx/5xx surface as unhealthy with actionable reason.

## Anchor files
- `api/v1/report_schedules.py` — `_coerce_channel`, defensive `_to_response`, wrapped `list_report_schedules`
- `api/v1/chat.py` — `_extract_readable`, recursive `_format_agent_output`
- `api/v1/knowledge.py` — wrapped `search_knowledge`
- `core/schemas/api.py` — `SchemaCreate._validate_json_schema`
- `connectors/framework/base_connector.py` — HTTP-status-aware `health_check`
- `ui/src/pages/Schemas.tsx` — union defaults + custom in card grid + Total counter
- `ui/src/pages/ConnectorCreate.tsx` — Extra config JSON textarea
- `ui/src/pages/ConnectorDetail.tsx` — Extra config JSON + OAuth2 Client Secret + Refresh Token
- `tests/unit/test_bug_sweep_24apr.py` — 30 regression tests
- `scripts/generate_24apr_summary_xlsx.py` — summary generator (xlsx lives in Downloads, not committed)

## Discipline lessons banked
See `feedback_24apr_reopen_autopsy.md` — four-class pattern (single bad row/field poisons whole surface), sibling-sweep checklist, "tester's error payload is the diagnostic" rule.
