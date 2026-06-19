# Aishwarya 19 June 2026 Bugfix Learnings

## What Reopened

The reopened cases had one common cause: earlier fixes stopped at the visible symptom and did not audit sibling runtime paths.

- HubSpot scope handling mixed read readiness and write permission gaps into one "missing scope" state. A healthy CRM read connection could still look blocked because optional/write scopes were projected into the contract badge.
- SendGrid stats accepted only the most direct `start_date` shape. Workflow/tool callers can wrap arguments under `kwargs`, `params`, `arguments`, or free-text prompts.
- Workflow HubSpot contact fetch worked as a standalone connector call but not as a workflow step because the workflow engine routed it through generic LLM agent execution.
- HITL resume updated only some existing step fields and only when status changed. Output, errors, counters, and later HITL pauses could stay stale.
- The workflow run page stopped polling in `waiting_hitl`, so a browser opened before approval could miss the resumed run entirely.
- Failed steps exposed only a status badge; structured provider errors were persisted but not rendered for operators.

## Permanent Rules

- Read and write readiness must remain separate in backend state, API payloads, and UI tables.
- Connector tools used by workflows need deterministic workflow step execution; do not rely on an LLM agent path for direct connector calls.
- Resume paths must share persistence logic with first-run paths. Do not duplicate insert-only step sync code.
- UI polling must continue for paused non-terminal workflow states that can transition externally.
- Error payloads must be structured and displayed as code, message, and details. Never collapse provider failures into a bare "Failed" badge.
- Regression tests must include wrapped/tool-caller input shapes, not only hand-written direct function calls.

## Regression Pins Added

- HubSpot healthy private-app partial scope handling in CMO setup and connector contracts.
- SendGrid `get_stats` wrapped arguments, prompt text parsing, and invalid-date fail-fast behavior.
- Workflow `fetch_hubspot_contacts` alias execution through the connector tool adapter.
- Structured connector-tool failure propagation.
- Workflow parser support for `connector_tool`.
- Playwright coverage for CMO contract read/write separation, HITL run polling, and failed step detail rendering.
