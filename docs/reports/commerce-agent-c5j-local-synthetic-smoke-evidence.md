# Commerce Agent C5J Local Synthetic Smoke Evidence

Date: 2026-05-26

Scope: local/dev/temp-smoke evidence for the merged C5I synthetic merchant dataset. This report records scrubbed command outcomes only. It does not include raw logs, secrets, private merchant details, provider credentials, production config, or production allowlist values.

## Worktree

- Repo: AgenticOrg
- Worktree used: `C:\tmp\agentic-org-c5j-smoke-prep`
- Evidence input directory: `C:\tmp\agentic-org-c5j-smoke-prep\.tmp\c5j`
- Main SHA tested: `508d34b06b720c1686104afe9758511c99fb58cb`
- C5I merge baseline: `508d34b06b720c1686104afe9758511c99fb58cb`

## Synthetic Dataset IDs

These IDs are internal/local/smoke-only synthetic values. They are not production allowlist values and are not merchant approval evidence.

- Dataset version: `c5i-synth-v1`
- Merchant ID: `mch_synth_internal_smoke_0001`
- Agent ID: `cag_synth_internal_smoke_sales_0001`
- Product ID: `cprd_synth_internal_smoke_widget_0001`
- Variant ID: `cvar_synth_internal_smoke_widget_0001_a`
- Provider marker: `mock`
- Placeholder URL host: `commerce-synth-smoke.example.invalid`
- Currency placeholder: `ZZZ`

## Commands Run

- `git rev-parse HEAD`
- `python scripts\validate_commerce_c5i_synthetic_dataset.py`
- `python -m pytest tests\regression\test_commerce_agent_c5i_synthetic_dataset.py -q`
- `python -m pytest tests\unit\test_a2a_mcp.py tests\regression\test_commerce_sales_agent_no_provider_calls.py -q`
- `python -m ruff check scripts\validate_commerce_c5i_synthetic_dataset.py tests\regression\test_commerce_agent_c5i_synthetic_dataset.py`
- `git diff --check`
- Focused secret scan on evidence and C5I docs/dataset/validator/test files
- Focused private-detail scan on evidence and C5I docs/dataset files
- Focused overclaim scan on evidence and C5I docs/dataset files
- Focused synthetic safety scan on evidence and C5I docs/dataset files

## Result Summary

| Check | Result | Scrubbed Evidence |
| --- | --- | --- |
| Main SHA capture | Pass | `agenticorg-main-head.log` |
| C5I synthetic dataset validator | Pass | `agenticorg-c5i-validator.log` |
| C5I synthetic dataset regression | Pass, 9 tests passed | `agenticorg-c5i-regression.log` |
| Gated discovery and no-provider-call tests | Pass, 18 tests passed | `agenticorg-discovery-no-provider.log` |
| Ruff on C5I files | Pass | `agenticorg-ruff.log` |
| `git diff --check` | Pass | `agenticorg-diff-check.log` |
| Secret scan | Pass, no matches | `agenticorg-secret-scan.log` |
| Private-detail scan | Pass, no matches | `agenticorg-private-detail-scan.log` |
| Overclaim scan | Pass, no matches | `agenticorg-overclaim-scan.log` |
| Synthetic safety scan | Pass, no matches | `agenticorg-synthetic-safety-scan.log` |

The AgenticOrg gated discovery and no-provider-call tests passed. They confirmed that public MCP/A2A commerce discovery stays hidden by default and that Commerce Sales Agent tooling remains Grantex-only with no direct Stripe, Plural, Pine, or provider credential path.

## Safety Assertions

- No deploy was run.
- No cloud command was run.
- No production config was changed.
- No production discovery flag was changed.
- No production allowlist value was set.
- `AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED` remains production-disabled.
- `COMMERCE_PUBLIC_DISCOVERY_ENABLED` remains production-disabled.
- `COMMERCE_PUBLIC_DISCOVERY_MERCHANT_ALLOWLIST` remains production-unset.
- `COMMERCE_V1_ENABLED` was not enabled in production.
- `COMMERCE_LIVE_MODE_ENABLED` and `PLURAL_LIVE_ENABLED` remain off.
- No live payment flow was run.
- No live Plural flow was run.
- No checkout/payment production creation request was run.
- No direct Stripe, Plural, Pine, or provider credential path was introduced.
- No secrets or real merchant data were required.
- Synthetic data is not production approval.
- The synthetic merchant ID must not be used in any production allowlist.

## Remaining Blockers

- No real named merchant approval exists.
- Grantex production read-only discovery remains fail-closed.
- AgenticOrg public commerce discovery remains gated.
- Real C5I artifact intake remains blocked until human approvals are provided.
