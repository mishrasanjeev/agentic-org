## Configure and test guardrails

Tenant-scoped rules evaluate text at the input, retrieved-evidence, model-output and tool-action stages. A rule names a detector, threshold, optional agent/use-case/risk-tier scope, priority and response. Detector identifiers in the API are `sensitive_data`, `toxicity`, `pattern`, `injection` and `output_policy`. Action values are `flag`, `mask`, `redact`, `tokenise` and `block`; API validation enforces detector/action compatibility.

Guardrail rule administration is currently an authenticated tenant-administrator API, not a dedicated console page. Use an approved API client with the tenant selected and the required `governance.guardrails.sensitive.read` or `.write` permission. The API is under `/api/v1/guardrails`: `GET /status`, `GET/POST /rules`, `PATCH/DELETE /rules/{rule_id}` and `POST /evaluate`. Do not copy a privileged key into a browser console or commit it to a script.

```flow
Check effective status | Read /guardrails/status and confirm whether the tenant is flag-only or enforced.
Create a narrow rule | Choose one stage, detector, threshold, response and explicit scope.
Dry-run examples | Call /guardrails/evaluate with synthetic allowed and flagged text; inspect transformed text and outcomes.
Review detector limits | Check false positives, missed cases, latency and failure behavior with the domain owner.
Enable only through approved controls | Confirm hooks and tenant enforcement configuration before relying on blocking or transforms.
Monitor and revise | Correlate rule outcomes with run evidence; keep a rollback owner and test after edits.
```

## Understand the modes

Rules can be stored while platform hooks or enforcement are off. Hooks are disabled by default. With hooks enabled but tenant enforcement off, evaluations are flag-only: the request continues unchanged while findings are recorded for observation. The `/evaluate` endpoint is a dry run: it returns the result the rules would produce, including transformed text or `allowed: false`, but does not enforce that result on a live call and does not write an audit outcome. A dry run is therefore not evidence that production traffic is protected.

Enforcement is a separate setting. When active, a matching transform changes the text before it continues, and a blocking rule refuses that stage. A strict runtime may refuse a stage if it cannot safely load rules or a detector fails; behavior depends on the deployed strictness configuration. Confirm the effective status, hook configuration and runtime logs with your platform owner rather than inferring enforcement from the existence of a rule.

## Scope and detector behavior

Rules match one of four stages: `input`, `retrieval`, `output` or `action`. Optional `agent_id`, `use_case` and `risk_tier` fields narrow a rule. Without those fields, the rule can apply to every call at that stage. Rules run in priority order, and a block takes precedence over a transform. Pattern rules are bounded and reject known unsafe regular-expression shapes; keep patterns short, specific and reviewed.

Sensitive-data recognition includes configured patterns for common identifiers such as payment cards, Aadhaar, PAN, GSTIN, email, UPI and phone. Recognition quality depends on the active analyzer and fallback recognizers. Toxicity and prompt-injection detection are signals, not proof of intent or a complete security boundary. Test representative languages, formats, OCR noise and adversarial inputs. Retain a human review path for high-impact decisions.

## Operational checklist

- Start in flag-only mode with synthetic cases and inspect false positives and misses.
- Verify exact tenant, agent, use-case and risk-tier matching, including missing-context behavior.
- Test each action separately; ensure masks/redactions do not destroy required business context.
- Verify that blocked input, retrieval, output and tool-action cases stop at the intended boundary.
- Confirm audit permissions, correlation IDs, retention and incident ownership.
- Keep a rollback rule/configuration and rerun regression cases after every detector or policy change.

Guardrails do not replace access control, data minimization, provider contracts, connector permissions, human approvals or institution policy. They are one control in a layered system.

Next: [Model Gateway](/docs/model-gateway), [Security and data](/docs/security-and-data), [Audit and monitoring](/docs/audit-and-monitoring), [Troubleshooting](/docs/troubleshooting).
