## What you can do with AgenticOrg

AgenticOrg is a workspace for building and operating AI agents around your procedures, documents and approved business tools. It brings agents, knowledge, workflows, approvals and run evidence together. You decide the task and the boundaries; an agent is not a replacement for your organization's authority to make a financial, legal or customer decision.

You can start with a document question, then progress to a repeatable workflow. You do not need to connect every system before the first useful task. A model and a small, approved knowledge set are enough for a read-only learning exercise.

```flow
Choose a task | Pick one narrow job with a clear owner and a measurable outcome.
Add knowledge and tools | Supply current procedures and only the permissions required.
Test with sample data | Inspect evidence, errors and refusals before relying on outputs.
Run under review | Assign operators, approvals, support and change control.
```

## Choose your learning path

| Your role | Start with | Then learn |
| --- | --- | --- |
| First-time user | [Your first useful agent](/docs/first-agent) | [Knowledge and OCR](/docs/knowledge-and-ocr), [Run and chat](/docs/run-and-chat) |
| Administrator | [Workspace and roles](/docs/workspace-and-roles) | [Model setup](/docs/model-setup), [Connectors](/docs/connectors), [Security](/docs/security-and-data) |
| Process owner | [Create agents](/docs/create-agents) | [Workflows](/docs/workflows), [Approvals](/docs/approvals), [Evaluation](/docs/evaluate-and-rollout) |
| Bank or fintech team | [Business onboarding](/docs/bfsi-business-onboarding) | [Reconciliation](/docs/bfsi-reconciliation), [Customer service](/docs/bfsi-customer-service) |
| Merchant team | [Commerce](/docs/commerce) | [BFSI merchant services](/docs/bfsi-merchant-services) |
| Developer or IT team | [API, SDK, MCP and A2A](/docs/api-sdk-mcp) | [Self-hosting](/docs/self-hosting) |

## Before you begin

1. Obtain an account at [app.agenticorg.ai](https://app.agenticorg.ai) or your organization's reviewed deployment. Accept an invitation if your organization already exists; do not create a second organization simply to join your team.
2. Ask the administrator which company and role you should use. Company selection matters for business data and connector access.
3. Confirm that a model provider is configured for your task. Your login does not automatically provide every external provider credential.
4. Use synthetic or approved non-sensitive material for your first exercise. Do not upload customer identity documents as a trial.
5. Know who can approve outputs and who owns the connected system.

## What availability means

**Implemented** means a runtime path exists. **Configuration-dependent** means it also needs credentials, permissions, worker infrastructure or tenant settings. **Example** means a teaching scenario, not a pre-integrated solution. A listed connector, agent template, voice provider or channel is not proof of successful live execution in your workspace.

Shopify read-only commerce sync and the signed Twilio voice runtime have specific implemented paths. Other commerce sources and voice-provider options do not automatically have equivalent runtime support. OACP buyer answers and prepared handoffs do not themselves create orders or collect money. Jev advisory integration is access-gated and is not an active production decision authority.

## Find the right screen

These links open the hosted application. Sign in first and select the correct
company. On a self-managed deployment, use its hostname with the same path.
Some screens are restricted by role; a missing menu is not an invitation to
bypass permissions.

| I want to... | Screen | Read first |
| --- | --- | --- |
| Choose a company | [Companies](https://app.agenticorg.ai/dashboard/companies) | [Workspace and roles](/docs/workspace-and-roles) |
| Connect a model | [AI Provider Credentials](https://app.agenticorg.ai/dashboard/settings/ai-credentials) | [Model setup](/docs/model-setup) |
| Find or create an agent | [Agents](https://app.agenticorg.ai/dashboard/agents) | [Create agents](/docs/create-agents) |
| Build from a procedure | [Create from SOP](https://app.agenticorg.ai/dashboard/agents/from-sop) | [Create agents](/docs/create-agents) |
| Upload a document | [Knowledge Base](https://app.agenticorg.ai/dashboard/knowledge) | [Knowledge and OCR](/docs/knowledge-and-ocr) |
| Connect a business service | [Connectors](https://app.agenticorg.ai/dashboard/connectors) | [Connectors](/docs/connectors) |
| Coordinate several steps | [Workflows](https://app.agenticorg.ai/dashboard/workflows) | [Workflows](/docs/workflows) |
| Review proposed actions | [Approvals](https://app.agenticorg.ai/dashboard/approvals) | [Human review](/docs/approvals) |
| Review business onboarding | [Governed cases](https://app.agenticorg.ai/dashboard/approvals/cases) | [Governed cases](/docs/governed-cases) |
| Configure voice | [Voice Setup](https://app.agenticorg.ai/dashboard/voice-setup) | [Voice](/docs/voice) |
| Configure browser automation | [RPA](https://app.agenticorg.ai/dashboard/rpa) | [RPA](/docs/rpa) |
| Investigate run evidence | [Audit](https://app.agenticorg.ai/dashboard/audit) | [Monitoring](/docs/audit-and-monitoring) |
| Configure merchant commerce | [Commerce Runtime](https://app.agenticorg.ai/dashboard/commerce-runtime) | [Commerce](/docs/commerce) |
| Connect an external agent client | [A2A / MCP](https://app.agenticorg.ai/dashboard/integrations) | [API and clients](/docs/api-sdk-mcp) |
| Create an API key | [Settings](https://app.agenticorg.ai/dashboard/settings) | [API and clients](/docs/api-sdk-mcp) |
| Review subscription or usage | [Billing](https://app.agenticorg.ai/dashboard/billing) | [Billing and support](/docs/billing-and-support) |

## When you get stuck

Use [Troubleshooting](/docs/troubleshooting) to isolate the issue. Record the screen, time, selected company, action and safe error code. Never send passwords, API keys or raw customer files in a support ticket.

The guides describe source-verified behavior, not a promise that your deployment has every optional feature enabled. Ask your administrator about the deployed version and tenant configuration.
