## Configure and test guardrails

Tenant-scoped rules evaluate text at the input, retrieved-evidence, model-output and tool-action stages. A rule names a detector, threshold, optional agent/use-case/risk-tier scope, priority and response. Detector identifiers are `sensitive_data`, `toxicity`, `pattern`, `injection`, `output_policy` and `grounding`. Action values are `flag`, `mask`, `redact`, `tokenise` and `block`; API validation enforces detector/action compatibility.

Tenant administrators manage rules on the [Guardrails](https://app.agenticorg.ai/dashboard/settings/guardrails) page. It shows the mode in effect (hooks off, flag-only or enforcing), lists the rules with what each applies to, adds, changes, enables, disables and deletes a rule, and dry-runs a stage over a text. Detector options are edited as JSON, starting from an example for the chosen detector. The page changes rules only; hooks and tenant enforcement are switched on through their own approved controls.

The same operations are available to an approved API client with the required `governance.guardrails.sensitive.read` or `.write` permission, under `/api/v1/guardrails`: `GET /status`, `GET/POST /rules`, `PATCH/DELETE /rules/{rule_id}` and `POST /evaluate`. Do not copy a privileged key into a browser console or commit it to a script.

```flow
Check effective status | Open the Guardrails page and read the banner: hooks off, flag-only or enforcing.
Create a narrow rule | Choose one stage, detector, threshold, response and explicit scope.
Dry-run examples | Use the dry run with synthetic allowed and flagged text; inspect the outcomes and the text after the rules.
Review detector limits | Check false positives, missed cases, latency and failure behavior with the domain owner.
Enable only through approved controls | Confirm hooks and tenant enforcement configuration before relying on blocking or transforms.
Monitor and revise | Correlate rule outcomes with run evidence; keep a rollback owner and test after edits.
```

## Understand the modes

Rules can be stored while platform hooks or enforcement are off. Hooks are disabled by default. With hooks enabled but tenant enforcement off, evaluations are flag-only: the request continues unchanged while findings are recorded for observation. The `/evaluate` endpoint is a dry run: it returns the result the rules would produce, including transformed text or `allowed: false`, but does not enforce that result on a live call and does not write an audit outcome. A dry run is therefore not evidence that production traffic is protected.

Enforcement is a separate setting. When active, a matching transform changes the text before it continues, and a blocking rule refuses that stage. A strict runtime may refuse a stage if it cannot safely load rules or a detector fails; behavior depends on the deployed strictness configuration. Confirm the effective status, hook configuration and runtime logs with your platform owner rather than inferring enforcement from the existence of a rule.

## Scope and detector behavior

Rules match one of four stages: `input`, `retrieval`, `output` or `action`. Optional `agent_id`, `use_case` and `risk_tier` fields narrow a rule. Without those fields, the rule can apply to every call at that stage. Rules run in priority order, and a block takes precedence over a transform. Pattern rules are bounded and reject known unsafe regular-expression shapes; keep patterns short, specific and reviewed.

Sensitive-data recognition includes configured patterns for common identifiers such as payment cards, Aadhaar, PAN, GSTIN, email, UPI and phone. Recognition quality depends on the active analyzer and fallback recognizers. Toxicity and prompt-injection detection are signals, not proof of intent or a complete security boundary. A `grounding` rule (output stage; flag or block) compares each sentence of an answer with what the run retrieved and reports claims, and figures, that the retrieved text does not carry. It is a word-level check, not a judgement of meaning: it catches answers written without the retrieved material, and it can flag a faithful paraphrase. In the dry run, paste the retrieved text as the context. Test representative languages, formats, OCR noise and adversarial inputs. Retain a human review path for high-impact decisions.

## Operational checklist

- Start in flag-only mode with synthetic cases and inspect false positives and misses.
- Run the adversarial set on the Guardrails page against your rules. It reports, per category, the synthetic attacks your rules detect, the ones they miss and the benign texts they wrongly catch. Some attacks are included because pattern-based detectors miss them, so a score below 100% is expected; the result describes that corpus only.
- Verify exact tenant, agent, use-case and risk-tier matching, including missing-context behavior.
- Test each action separately; ensure masks/redactions do not destroy required business context.
- Verify that blocked input, retrieval, output and tool-action cases stop at the intended boundary.
- Confirm audit permissions, correlation IDs, retention and incident ownership.
- Keep a rollback rule/configuration and rerun regression cases after every detector or policy change.

Guardrails do not replace access control, data minimization, provider contracts, connector permissions, human approvals or institution policy. They are one control in a layered system.

Next: [Model Gateway](/docs/model-gateway), [Security and data](/docs/security-and-data), [Audit and monitoring](/docs/audit-and-monitoring), [Troubleshooting](/docs/troubleshooting).
