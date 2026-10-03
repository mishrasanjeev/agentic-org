## Scenario and boundary

Example Bank wants a service assistant that explains approved procedures, triages complaints and prepares staff responses through chat and configured voice. Start with general information and synthetic cases. Do not expose account details or perform account/card/payment actions without the bank's approved authentication and integration path.

A phone number, claimed name or free-text account identifier is not identity verification. AgenticOrg's voice/runtime controls do not themselves establish bank-customer authentication.

## Prepare a reviewed knowledge set

Upload current product FAQs, branch directory, complaint procedure and escalation policy. Assign effective dates and an owner. Search known answers and confirm citations. Remove or clearly supersede conflicting old procedures. Do not load unapproved financial advice as an operational policy.

## Build the service agent

Use a reviewed support-triage candidate or custom operations agent. Define categories, urgency, response-draft structure and escalation. Grant read-only knowledge/approved ticket tools first. Explicitly refuse PIN/OTP collection, balance invention, account changes and payment instructions outside the authorized bank workflow.

1. Run a general question through the agent detail/chat path.
2. Test a complaint with missing information.
3. Test a suspected fraud/card-loss scenario requiring urgent human routing.
4. Ask for a private account balance and confirm refusal/referral.
5. Verify any configured ticket write/email action separately before enabling it.

## Process map

This is a response-preparation path. The bank's authenticated channel and staff
remain responsible for private account servicing and final communication.

```flow
Question or complaint | Receive the request through a configured general-information or authenticated channel. | Owner: Bank channel owner | If blocked: A claimed name or phone number alone does not establish customer identity.
Knowledge check | Retrieve the current approved procedure and inspect its cited source. | Owner: Service agent and knowledge owner | If blocked: Missing, conflicting or outdated guidance goes to staff; do not invent an answer.
Response draft | Prepare a bounded answer or complaint summary from supported facts. | Owner: Service agent | If blocked: Private account data or an unsupported action is refused or referred to the bank's authorized path.
Human escalation | Review sensitive, urgent or unresolved requests before any staff response. | Owner: Authorized service staff | Human decision: Staff decide whether to send, revise or escalate the prepared response. | If blocked: Suspected fraud or a failed voice/ticket path follows the bank's urgent human process.
Confirmed outcome | Record communication and resolution in the bank's approved system. | Owner: Bank service team and system of record | If blocked: A prepared draft is not a sent message or completed account action.
```

## Add voice only after chat is correct

Configure signed Twilio calls using [Voice setup](/docs/voice). Test with an approved number/window. Check speech recognition, spoken clarity, interruption/silence, safe failure wording and actual provider status. A passing text simulation does not prove acoustic or telephone quality.

Document provider recording/retention and customer-facing disclosures under the institution's process. AgenticOrg's call-history masking does not control the provider's own storage settings.

## What to measure

Track correct routing, unsupported-answer rate, unresolved questions, escalation time, call failures and human overrides. Validate different accents/languages and noisy conditions where they are in scope. Do not claim a language is ready merely because it appears in a selector.

Roll out a narrow task population before sensitive account servicing. Re-test whenever the bank procedure, provider number/callback, model or ticketing contract changes.

Next: [Run and chat](/docs/run-and-chat), [Knowledge and OCR](/docs/knowledge-and-ocr), [Approvals](/docs/approvals).
