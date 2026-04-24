---
name: 24-Apr reopen autopsy — why TC_001 kept coming back + three sibling bug classes
description: Brutal post-mortem of the 24-Apr reopen of TC_001 (report-schedules 500). Same root-cause pattern appeared in three other reported bugs that day. Read before closing any bug whose symptom is an opaque 500 or a false-positive "healthy" report.
type: feedback
originSessionId: 50c2ef0e-fce4-4b61-9681-f2bdcd1a87db
---
The 24-Apr sweep (2026-04-24) had the tester re-opening TC_001 for the SECOND time, and four other tickets turned out to be the same bug class. Here is what the earlier sweeps missed and the rules that now govern them.

## The pattern: single bad row / single bad field crashes the whole surface

Four distinct user reports on 24-Apr were the same failure mode:

1. **TC_001 reopen + Uday/Ramesh RA-ReportSched**: `GET /report-schedules` returned `E1001 INTERNAL_ERROR`. Root cause: one legacy row with `recipients=["user@example.com"]` (v4.4-era string format) crashed `_to_response`'s Pydantic DeliveryChannel parsing for the whole list.
2. **TC_008**: chat bubbles rendered `{'type':'text','text':'…','extras':{'signature':'…'}}` verbatim. Root cause: `_format_agent_output` did `str(val)` when `answer` was itself a dict, producing Python repr (single-quoted) that JSON.parse can't recover.
3. **TC_006**: Schema registry accepted `json_schema={}` and the row rendered blank in view/edit (TC_004/005). Root cause: `SchemaCreate.json_schema` was typed `dict[str, Any]` with no shape validation.
4. **Uday Gmail UI-HEALTH-404**: `/connectors/{id}/test` returned `{"status":"healthy","http_status":404}`. Root cause: base connector `health_check` reported "healthy" whenever the HTTP call didn't raise — it ignored the response status.

All four are "strict validation/serialization applied at the boundary, but the boundary was blind to one specific input shape".

**Why:** The 22-Apr/23-Apr "Partially closed" verdicts were incremental — fix the POST 500, leave the GET untouched; fix `/connectors/{id}/test` error messages, leave the `healthy/404` false positive. Incremental fixes leave siblings open. For bug classes where multiple surfaces share a helper, fix THE HELPER and sweep every caller.

**How to apply:**

- If a symptom is an opaque 500 / `E1001 INTERNAL_ERROR`, the route body MUST be wrapped in `try/except → HTTPException(500, detail="actionable message")`. The global 500 handler is a last resort, not a product feature.
- A list endpoint must NEVER let one bad row poison the whole response. Per-row `try/except` → log + skip is the pattern. (See `api/v1/report_schedules.py::list_report_schedules`.)
- When converting structured output to a display string, NEVER `str(dict)` — use a recursive extractor that walks known text-carrying keys (`text`, `content`, `answer`, `response`, `message`, `summary`, `result`). See `api/v1/chat.py::_extract_readable`.
- JSON-accepting endpoints (schemas, configs, filters) must validate shape beyond "is it a dict". Minimum for JSON Schema: non-empty + `type` or `$ref`; `type=object` must have non-empty `properties`.
- Health checks that probe an HTTP endpoint must inspect `status_code`, not just "call didn't raise". 2xx/3xx = healthy; 4xx/5xx = unhealthy with the status preserved in the response payload for debugging.

## Sibling-sweep checklist before closing any bug whose symptom is a 500

Before a bug with an opaque-500 symptom can be verdicted `Fixed`, grep for:

- `return [_to_response(r) for r in …]` → add per-row try/except + route-level wrapper.
- `str(` near response assembly in `api/v1/chat.py`, `api/v1/agents.py`, `api/v1/workflows.py` → swap to the readable-extractor pattern.
- `dict[str, Any]` Pydantic fields on `POST` / `PUT` bodies that store JSON blobs → add a `@field_validator` with minimum-shape rules.
- Connector `health_check` overrides that return `{"status": "healthy", ...}` from a raw HTTP call → gate on `200 <= sc < 400`.

## When the tester's reopen payload itself is the diagnostic

The 24-Apr tester pasted:
```json
{"code":"E1001","name":"INTERNAL_ERROR","message":"Internal server error","severity":"error","retryable":true,"timestamp":"…"}
```

That payload is the `api/error_handlers.py::server_error` shape. Seeing it in a reopen ticket is an authoritative signal that the route body is NOT wrapping its own exceptions. Do not accept "probably a cache issue" or "likely deploy lag" as triage — the shape of the error proves the route has no try/except.

## Anchor files

- `api/v1/report_schedules.py::_coerce_channel`, `_to_response`, `list_report_schedules` — defensive list + per-row skip
- `api/v1/chat.py::_extract_readable`, `_format_agent_output` — recursive text extraction
- `core/schemas/api.py::SchemaCreate._validate_json_schema` — minimum shape validation
- `connectors/framework/base_connector.py::health_check` — HTTP-status-aware health
- `api/v1/knowledge.py::search_knowledge` — wrapped search with structured 500
- `tests/unit/test_bug_sweep_24apr.py` — 30 regression tests covering all four patterns
