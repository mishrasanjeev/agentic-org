# AgenticOrg Bug Sheet Fix Summary - 2026-09-21

## Scope

Source: `AgenticOrg  Bugs.xlsx` supplied by QA.

The workbook contains two populated records. Both are valid high-severity
bugs, not enhancements. They were reported against the Agent and AI Agent
Generation flows.

## BUG-01 - Shadow sample rejected despite a healthy connector

**Verdict:** Fixed in code; deployed-environment recheck pending.

**Observed contract failure:** the UI/readiness path could present a healthy
tenant-global connector while a company-bound agent dispatch checked only the
company-scoped `ConnectorConfig` row. The runtime then reported
`connector_not_ready_for_dispatch` and named otherwise valid connectors as
missing.

**Root cause:** connector scope precedence was not shared by config resolution
and dispatch readiness. The company binding was treated as mandatory even
when the valid tenant-global binding was the configured source of record.

**Correction:** both config resolution and readiness now use the same safe
precedence:

1. exact `(tenant, company, connector)` binding;
2. tenant-global `(tenant, null company, connector)` binding;
3. no fallback to another company.

The fail-closed behavior remains intact: missing, unhealthy, undecryptable,
or cross-company credentials still block dispatch.

**Regression evidence:** backend tests cover exact-company lookup, tenant-
global fallback, and other-company refusal. Playwright replays the Shadow
Mode `Generate Test Sample` workflow and asserts the visible sample-count
update using deterministic fixtures.

**Reopened 2026-10-22 (production):** a company-bound agent using a
tenant-global connector was still refused with `connector_not_ready_for_dispatch`
/ `missing_connector_config`. Step 2 above was unreachable: `connector_configs`
RLS (`v6z6`) shows a session only the rows of its exact company context, and
the fallback query ran inside the company-scoped session. The unit tests faked
a session that could see the tenant-global row, which the real policy never
allows. The tenant-global lookup now runs in a tenant-scoped session
(`api/v1/agents.py` `_tenant_global_connector_config`), for name and id
lookups, in both config resolution and dispatch readiness. The RLS policy is
unchanged, so company rows stay isolated. Evidence: the unit fakes now apply
the policy, and `tests/integration/test_rls_company_tenant_connector_fallback.py`
runs as the NOBYPASSRLS app role (tenant-global fallback works; another
company's and a company row from a tenant-level agent stay refused); both
fail on the previous code. The tool gateway (`core/tool_gateway/gateway.py`)
keeps its exact-company lookup, as `tests/security/test_connector_company_scope.py`
requires.

## BUG-02 - Agent generation ignores tenant-configured LLM credentials

**Verdict:** Fixed in code; deployed-environment recheck pending.

**Observed contract failure:** the Create Virtual Employee flow returned
`llm_generation_failed` even though the tenant had configured Gemini.

**Root cause:** `/agents/generate` had the authenticated tenant ID but called
the generator without it. The generator then called the router without tenant
context, so tenant-owned credential resolution and tenant budget policy were
not reliably applied.

**Correction:** tenant context now travels from the authenticated route to the
generator, router, provider secret resolver, and Gemini/Claude/OpenAI provider
calls. Missing configuration remains an actionable 503; provider failures
remain distinguishable from configuration errors.

**Regression evidence:** backend tests assert the tenant ID reaches the LLM
boundary and that the router resolves a tenant-owned credential. Playwright
replays Create Virtual Employee, submits a natural-language description, and
asserts the generated suggestion is rendered.

## Why these cases reopened

The earlier fixes were too close to the symptom boundary. A connector status
or registration row was treated as proof that the exact runtime binding was
dispatchable, and a provider-specific generation failure was treated as an
LLM availability problem without tracing tenant context through the full
call chain. Those approaches allowed the UI, route, resolver, and runtime to
disagree.

The permanent prevention is now in `docs/bug_triage_skill.md` Rules 17-19:
shared scope precedence, mandatory tenant propagation, and a browser
regression for every reopened user flow.

## Validation policy

Local backend tests, type checks, lint, and Playwright fixtures are required
before the code can be marked fixed. This report intentionally does not copy
QA credentials, tokens, cookies, or private URLs. A deployed bug status must
be upgraded only after the exact QA account and environment re-run the same
steps against the fix commit.
