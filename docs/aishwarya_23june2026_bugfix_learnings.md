# Aishwarya 23 June 2026 Bugfix Learnings

## Why These Cases Reopened

The reopened cases were valid. The earlier fixes were too narrow because they patched one observed payload shape or one UI route instead of auditing the full contract between connectors, workflows, approvals, and run visibility.

- HubSpot readiness trusted only a small set of scope field names. Tester evidence used configured scopes plus registered CRM tools, but the contract path did not normalize all equivalent scope payloads.
- SendGrid accepted direct date arguments but not the wrappers and prompt text that agent/tool callers actually send.
- Google Ads passed malformed or incomplete date arguments through to the vendor, producing an opaque HTTP 400 instead of an actionable local validation error.
- Workflow contact retrieval used a generic CRM agent action (`process`) for a deterministic HubSpot connector operation, so standalone connector success did not prove workflow success.
- HITL approval could return success while resume silently stopped when workflow-run linkage, engine-run-id, or engine state was missing.
- The workflow details page showed definition metadata but not the latest execution progress, so an approved run looked stuck even when run state existed elsewhere.

## Permanent Rules

- Normalize families of payload names at boundaries. Scope and tool parameters must handle direct keys, nested health/auth metadata, JSON wrappers, and prompt text where those are valid caller shapes.
- Healthy connector auth is not enough proof for HubSpot CRM read readiness when tool evidence is present. Missing read scopes can be waived only when the CRM read tools are registered, while legacy private-app rows without any tool registry payload keep the established healthy-read fallback.
- Connector tools inside workflows must be routed deterministically through the connector adapter. Do not let known connector retrieval aliases fall through to generic LLM agent actions.
- Resume paths must never return silently after an approval. If resume cannot continue, mark the DB workflow run failed with a structured error code.
- Vendor connectors should validate required parameters before network calls and identify missing or invalid fields in the response.
- Workflow list/detail pages must expose recent run status and progress, and must poll paused live states that can advance externally.

## Regression Pins Added

- HubSpot configured-scope and registered-tool normalization across setup, contracts, and the legacy CRM read evaluator.
- SendGrid `get_stats` JSON wrapper, nested parameter, and prompt-text date parsing.
- Google Ads `get_campaign_performance` local validation and GAQL generation with explicit `segments.date`.
- Workflow `retrieve_hubspot_contacts` alias execution through the HubSpot `list_contacts` connector tool.
- HITL resume failure modes for missing engine context and missing engine state.
- Playwright coverage for CMO HubSpot read/write readiness and workflow detail latest-run progress polling.
