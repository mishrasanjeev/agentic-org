## Start with approved data

Use synthetic records when learning. Before real data, identify the data owner, classification, purpose, allowed model/providers, retention and access. Minimize what each task receives. A document being useful to a model is not permission to upload it.

For BFSI, customer identity, financial, health and account information can require institution-specific controls. This guide explains product boundaries; it is not legal advice or proof of regulatory compliance.

## Protect access and credentials

Use named accounts and least-powerful roles. Keep company context explicit. Store provider credentials through the approved protected form, secret manager or connector path; never in prompts, scripts, example files or screenshots.

Review API keys and delegated grants by scope, environment and owner. Rotate and revoke through the appropriate control. A machine credential is not a human session and cannot perform human-only case decisions.

## Understand where information goes

| Path | Review requirement |
| --- | --- |
| Model and embedding calls | Provider endpoint, permitted data, retention and contractual terms |
| Knowledge extraction | File access, extracted chunks, OCR quality, replacement and deletion |
| Voice | Provider recording/retention, approved number and encrypted bounded text |
| RPA | Approved domains, secret custody and screenshot/log content |
| Governed cases | Cited evidence, encrypted excerpts, decision authority and signed handoff |
| Commerce | Merchant scope, artifact freshness, public-safe projection and provider-owned execution |

AgenticOrg's controls do not automatically set the provider's retention or your bank's network configuration. Review those separately.

## Retention and deletion

Decide who can delete source documents, derived knowledge, retained excerpts and operational evidence. These are not identical resources. Governed-case excerpt deletion can remove retained passages while keeping references/digests and the memo. It does not silently erase every downstream copy or the institution's system-of-record obligation.

Ask the operator to verify backup/restore and erasure procedures in the actual environment. A backup configuration file is not proof that restoration works.

## Respond to an incident

Stop affected schedules/actions, revoke compromised credentials, preserve safe references and notify the designated incident owner. Report security issues privately through [Support](https://agenticorg.ai/support) or the repository's security process. Do not open a public issue containing customer documents, tokens or exploitable tenant identifiers.

A useful incident record states time, version, resource/run reference, affected path, observed symptom and mitigation. Keep customer material in the institution's approved evidence system.

Next: [Workspace and roles](/docs/workspace-and-roles), [Audit and monitoring](/docs/audit-and-monitoring), [Self-hosting](/docs/self-hosting).
